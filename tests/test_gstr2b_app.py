"""Unit tests for gstr2b_app_hack - the one-file app and the functions it runs in the browser."""

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from html.parser import HTMLParser
from unittest import mock

import gstr2b_app_hack as app
import gstr2b_workpaper_hack as wp
from gstr2b_reconciliation_hack import load_records, xlsx_sheet_names

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_example(folder):
    """The example month on disk; returns {'gstr2b': path, 'books': path, ...}."""
    paths = {}
    for name, text in app.example_files().items():
        paths[name] = os.path.join(folder, name)
        with open(paths[name], "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    return {
        "gstr2b": paths["GSTR2B_19AAAAA0000A1Z5_092026.csv"],
        "books": paths["purchase_register_sep26.csv"],
        "opening_books": paths["carry_forward_books_deferred.csv"],
        "opening_2b": paths["carry_forward_2b_pending.csv"],
    }


class RunJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.options = write_example(self.temp.name)
        self.options["outdir"] = os.path.join(self.temp.name, "out")

    def test_the_example_month_gives_the_figures_of_the_engine_demo(self):
        result = app.run_job(self.options)
        self.assertTrue(result["ok"], result)
        summary = result["summary"]
        table = {row["label"]: row for row in summary["table4"]}
        self.assertEqual(table["4(A)(5) All other ITC"]["total"], 11500.0)
        self.assertEqual(table["4(B)(2) Reclaimable"]["total"], 2000.0)
        self.assertEqual(table["4(D)(1) Reclaimed"]["total"], 1500.0)
        self.assertEqual(table["4(C) Net ITC"]["total"], 9500.0)
        self.assertEqual(table["4(A)(5) All other ITC"]["values"], [7000.0, 2250.0, 2250.0, 0.0])
        bridge = {row["label"]: row for row in summary["bridge"]}
        self.assertEqual((bridge["ITC per books"]["total"], bridge["Difference"]["total"]),
                         (8900.0, 0.0))
        self.assertEqual([c["ok"] for c in summary["checks"]], [True, True])
        self.assertEqual((summary["period"], summary["fy"], summary["gstin"]),
                         ("September 2026", "2026-27", "19AAAAA0000A1Z5"))
        self.assertEqual(summary["counts"]["gstr2b_documents"], 5)
        self.assertTrue(summary["actions"])
        self.assertIn("GSTR-3B Table 4", result["text"])

    def test_the_files_written_and_the_zip_of_them(self):
        result = app.run_job(self.options)
        names = [f["name"] for f in result["files"]]
        self.assertEqual(names[:4], [wp.workpaper_filename(wp.Period(2026, 9)),
                                     "carry_forward_books_deferred.csv",
                                     "carry_forward_2b_pending.csv", "summary.txt"])
        self.assertEqual(names[4], "GSTR2B_workpaper_Sep-26.zip")
        for item in result["files"]:
            self.assertTrue(os.path.getsize(item["path"]) > 0, item)
        with zipfile.ZipFile(result["files"][4]["path"]) as archive:
            self.assertEqual(sorted(archive.namelist()), sorted(names[:4]))
            self.assertIsNone(archive.testzip())

    def test_progress_is_reported_in_order(self):
        steps = []
        app.run_job(self.options, steps.append)
        self.assertEqual(steps, [
            "Reading the GSTR-2B download", "Reading the purchase register",
            "Reading last month's open items", "Matching GSTR-2B against the books",
            "Writing the Excel workpaper"])

    def test_nothing_from_an_earlier_run_is_left_in_the_folder(self):
        os.makedirs(self.options["outdir"])
        stray = os.path.join(self.options["outdir"], "old_run.txt")
        with open(stray, "w") as handle:
            handle.write("left over")
        self.assertTrue(app.run_job(self.options)["ok"])
        self.assertFalse(os.path.exists(stray))

    def test_options_reach_the_engine(self):
        self.options.update(client="Example Traders", cn_policy="2b", date_tolerance="5",
                            value_tolerance="250", tax_tolerance="20")
        result = app.run_job(self.options)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"]["client"], "Example Traders")

    def test_a_missing_choice_is_explained_in_plain_words(self):
        for key, words in (("gstr2b", "GSTR-2B download"), ("books", "purchase register")):
            options = dict(self.options, **{key: ""})
            result = app.run_job(options)
            self.assertFalse(result["ok"])
            self.assertIn(words, result["error"])

    def test_problems_with_the_files_come_back_as_answers_not_exceptions(self):
        missing = app.run_job(dict(self.options, gstr2b=os.path.join(self.temp.name, "nope.csv")))
        self.assertFalse(missing["ok"])
        self.assertTrue(missing["error"])
        broken = os.path.join(self.temp.name, "broken.xlsx")
        with open(broken, "wb") as handle:
            handle.write(b"this is not a workbook")
        result = app.run_job(dict(self.options, books=broken))
        self.assertFalse(result["ok"])
        self.assertIn("not a readable .xlsx", result["error"])
        no_month = app.run_job(dict(self.options, gstr2b=self.options["books"]))
        self.assertFalse(no_month["ok"])
        self.assertIn("month", no_month["error"])

    def test_a_tolerance_that_is_not_a_number_is_reported(self):
        with mock.patch("sys.stderr", io.StringIO()):
            result = app.run_job(dict(self.options, date_tolerance="abc"))
        self.assertFalse(result["ok"])
        self.assertIn("could not be read", result["error"])

    def test_a_surprise_is_still_an_answer(self):
        with mock.patch.object(wp, "run", side_effect=RuntimeError("boom")):
            result = app.run_job(self.options)
        self.assertEqual((result["ok"], result["unexpected"]), (False, True))
        self.assertIn("RuntimeError", result["error"])


class SheetListTests(unittest.TestCase):
    def test_sheet_names_of_a_workbook_and_nothing_for_anything_else(self):
        with tempfile.TemporaryDirectory() as folder:
            book = os.path.join(folder, "two.xlsx")
            from xlsx_writer_hack import Workbook
            workbook = Workbook()
            workbook.add_sheet("First").write(0, 0, "a")
            workbook.add_sheet("Second").write(0, 0, "b")
            workbook.save(book)
            self.assertEqual(app.list_sheets(book), ["First", "Second"])
            self.assertEqual(xlsx_sheet_names(book), ["First", "Second"])
            plain = os.path.join(folder, "plain.csv")
            with open(plain, "w") as handle:
                handle.write("a,b\n")
            self.assertEqual(app.list_sheets(plain), [])
            broken = os.path.join(folder, "broken.xlsx")
            with open(broken, "wb") as handle:
                handle.write(b"nope")
            self.assertEqual(app.list_sheets(broken), [])
            self.assertEqual(app.list_sheets(os.path.join(folder, "absent.xlsx")), [])
            with self.assertRaises(ValueError):
                xlsx_sheet_names(broken)


class ExampleTests(unittest.TestCase):
    def test_four_small_files_that_the_readers_accept(self):
        files = app.example_files()
        self.assertEqual(sorted(files), [
            "GSTR2B_19AAAAA0000A1Z5_092026.csv", "carry_forward_2b_pending.csv",
            "carry_forward_books_deferred.csv", "purchase_register_sep26.csv"])
        with tempfile.TemporaryDirectory() as folder:
            paths = write_example(folder)
            two_b, _ = load_records(paths["gstr2b"], "GSTR2B", side="portal")
            books, _ = load_records(paths["books"], "BOOKS", side="books")
            self.assertEqual((len(two_b), len(books)), (5, 5))
            self.assertEqual(sum(1 for r in two_b if r.sign_class == "C"), 1)

    def test_it_is_deterministic_and_invented(self):
        self.assertEqual(app.example_files(), app.example_files())
        text = "".join(app.example_files().values())
        found = set(re.findall(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", text))
        self.assertTrue(found <= {"19AAAAA1111A1Z1", "27BBBBB2222B1Z2", "24CCCCC3333C1Z3",
                                  "07DDDDD4444D1Z4", "36EEEEE5555E1Z5", "29FFFFF6666F1Z6"})


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self._open = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._open = [dict(attrs), ""]

    def handle_data(self, data):
        if self._open is not None:
            self._open[1] += data

    def handle_endtag(self, tag):
        if tag == "script" and self._open is not None:
            self.scripts.append(tuple(self._open))
            self._open = None


def scripts_of(page):
    parser = _Scripts()
    parser.feed(page)
    return parser.scripts


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = app.build_app(ROOT)
        with open(os.path.join(ROOT, app.TEMPLATE_FILE), encoding="utf-8") as handle:
            cls.template = handle.read()

    def test_the_engines_own_source_is_embedded_unchanged(self):
        scripts = scripts_of(self.page)
        self.assertEqual([s[0].get("id") for s in scripts], ["app-data", "worker-src", None])
        data = json.loads(scripts[0][1])
        self.assertEqual(sorted(data["sources"]),
                         sorted(f"{module}.py" for module in app.ENGINE_MODULES))
        for module in app.ENGINE_MODULES:
            with open(os.path.join(ROOT, module + ".py"), encoding="utf-8") as handle:
                self.assertEqual(data["sources"][module + ".py"], handle.read(), module)
        self.assertEqual(data["pyodide"], app.PYODIDE_BASE)
        self.assertEqual(data["example"], app.example_files())

    def test_the_page_is_ascii_data_and_never_closes_a_script_early(self):
        data = scripts_of(self.page)[0][1]
        self.assertTrue(data.isascii())
        self.assertNotIn("</", data)

    def test_the_committed_app_is_current(self):
        with open(os.path.join(ROOT, app.APP_FILE), encoding="utf-8", newline="") as handle:
            self.assertEqual(handle.read(), self.page,
                             "gstr2b_workpaper_app.html is stale - run python3 gstr2b_app_hack.py")
        self.assertEqual(app.main(["--check"]), 0)

    def test_check_fails_for_a_missing_or_stale_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "app.html")
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(app.main(["--check", "--out", target]), 1)
                with open(target, "w") as handle:
                    handle.write("old")
                self.assertEqual(app.main(["--check", "--out", target]), 1)
            with mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(app.main(["--out", target]), 0)
                self.assertEqual(app.main(["--check", "--out", target]), 0)
            with open(target, encoding="utf-8", newline="") as handle:
                self.assertEqual(handle.read(), self.page)

    def test_the_template_needs_exactly_one_place_for_the_data(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in app.ENGINE_MODULES:
                shutil.copy(os.path.join(ROOT, name + ".py"), folder)
            with open(os.path.join(folder, app.TEMPLATE_FILE), "w") as handle:
                handle.write("<html>no place for it</html>")
            with self.assertRaises(ValueError):
                app.build_app(folder)

    def test_the_page_has_no_way_to_send_data_anywhere(self):
        # The page's own code makes no network call at all; the only fetches are the engine
        # runtime's, loaded from the one allowed host, and the browser enforces that host.
        for forbidden in ("XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "fetch(",
                          "<form", "<iframe", "<img", "<link", "<object", "<embed"):
            self.assertNotIn(forbidden, self.template, forbidden)
        hosts = set(re.findall(r"https?://([^/'\"\s;]+)", self.template))
        self.assertEqual(hosts, {"cdn.jsdelivr.net"})

    def test_the_policy_lets_the_page_contact_the_engine_host_only(self):
        policy = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"',
                           self.template).group(1)
        directives = dict(part.strip().split(" ", 1) for part in policy.split(";") if part.strip())
        self.assertEqual(directives["default-src"], "'none'")
        self.assertEqual(directives["connect-src"], "https://cdn.jsdelivr.net")
        for name, value in directives.items():
            for host in re.findall(r"https?://[^\s]+", value):
                self.assertEqual(host, "https://cdn.jsdelivr.net", name)
        self.assertEqual(directives["form-action"], "'none'")
        self.assertTrue(app.PYODIDE_BASE.startswith("https://cdn.jsdelivr.net/"))

    def test_the_scripts_parse(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        for attrs, body in scripts_of(self.page)[1:]:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                             encoding="utf-8") as handle:
                handle.write(body)
            self.addCleanup(os.unlink, handle.name)
            result = subprocess.run([node, "--check", handle.name], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
