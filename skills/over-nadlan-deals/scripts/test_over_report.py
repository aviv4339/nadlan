"""Offline tests for over_report.fetch_deals (no network): python3 -m unittest test_over_report"""
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
import over_report  # noqa: E402


def deal(i, helka=1):
    return {"date": f"2025-01-{1 + i % 28:02d}", "amount": 1_000_000 + i, "gush": "100", "helka": str(helka),
            "sub_parcel": "001", "nature": "דירה בבית קומות"}


def fake_search(rows, total=None):
    """search_deals over a fixed row list, honouring limit/offset like the server."""
    def call_tool(server, tool, args):
        assert (server, tool) == ("deals", "search_deals")
        off, lim = args["offset"], args["limit"]
        return {"data": rows[off:off + lim], "total": len(rows) if total is None else total, "total_capped": False}
    return call_tool


class FetchDeals(unittest.TestCase):
    def run_fetch(self, rows, total=None, keep=None, max_deals=5000):
        with mock.patch.object(over_report, "call_tool", fake_search(rows, total)):
            return over_report.fetch_deals([({"gush": 100}, keep)], {}, [], max_deals)

    def test_identical_deals_across_page_boundary_are_all_kept(self):
        rows = [deal(i) for i in range(250)]
        rows[200] = dict(rows[199])  # same transaction values on the last row of page 1 and the first of page 2
        deals, notes, _ = self.run_fetch(rows)
        self.assertEqual(len(deals), 250)
        self.assertEqual(sum(d == rows[199] for d in deals), 2)
        self.assertEqual(notes, [])

    def test_keep_filters_helkas_after_paging(self):
        rows = [deal(i, helka=1 + i % 2) for i in range(230)]
        deals, notes, _ = self.run_fetch(rows, keep={2})
        self.assertEqual(len(deals), 115)
        self.assertTrue(all(d["helka"] == "2" for d in deals))
        self.assertEqual(notes, [])

    def test_total_mismatch_is_reported(self):
        deals, notes, _ = self.run_fetch([deal(i) for i in range(250)], total=251)
        self.assertEqual(len(deals), 250)
        self.assertEqual(len(notes), 1)
        self.assertIn("reported 251", notes[0])

    def test_cap_is_reported_as_cap(self):
        deals, notes, _ = self.run_fetch([deal(i) for i in range(450)], max_deals=400)
        self.assertEqual(len(deals), 400)
        self.assertEqual(len(notes), 1)
        self.assertIn("--max-deals 400", notes[0])


class PickArea(unittest.TestCase):
    def test_punctuation_variants_of_one_name_resolve(self):
        cands = [{"name": "'רמת אביב ג", "src": "polygon", "size": 1106790},
                 {"name": "רמת אביב ג'", "src": "polygon", "size": 1106790},
                 {"name": "שכונת רמת אביב ג", "src": "addresses", "size": 135}]
        self.assertEqual(over_report.pick_area(cands, "רמת אביב ג׳")["src"], "polygon")

    def test_distinct_names_are_ambiguous(self):
        cands = [{"name": "הצפון הישן - החלק הצפוני", "src": "polygon", "size": 1817524},
                 {"name": "הצפון הישן - החלק הדרומי", "src": "polygon", "size": 1348069}]
        with self.assertRaises(over_report.Ambiguous):
            over_report.pick_area(cands, "הצפון הישן")


class RestoreRooms(unittest.TestCase):
    def test_fractional_rooms_are_refilled_by_exact_key(self):
        raw = [{"gush": "6953", "helka": "55", "sub_parcel": "091", "date_src": "31/12/2024", "amount": 8450000, "rooms": None},
               {"gush": "6953", "helka": "56", "sub_parcel": "001", "date_src": "01/01/2025", "amount": 5000000, "rooms": None},
               {"gush": "6953", "helka": "57", "sub_parcel": "002", "date_src": "02/01/2025", "amount": 4000000, "rooms": 4}]
        seen = []

        def fake_sql(q, max_rows=1000):
            seen.append(q)
            return [{"g": "6953", "h": "55", "s": "091", "dd": "31/12/2024", "a": "8450000", "room_num": "4.5"}]
        with mock.patch.object(over_report, "sql", fake_sql):
            fixed = over_report.restore_rooms({"deals": "t"}, raw)
        self.assertEqual(fixed, 1)
        self.assertEqual([d["rooms"] for d in raw], [4.5, None, 4])
        self.assertIn("('6953','55','091','31/12/2024','8450000')", seen[0])  # register columns are text
        self.assertNotIn("'57'", seen[0])  # rows that already have rooms are not looked up


class NetworkErrors(unittest.TestCase):
    def test_timeouts_surface_as_mcp_error_not_traceback(self):
        import over_mcp
        with mock.patch.object(over_mcp.urllib.request, "urlopen", side_effect=TimeoutError("The read operation timed out")):
            with self.assertRaises(over_mcp.McpError) as ctx:
                over_mcp.post("https://www.over.org.il/deals/mcp", {})
        self.assertIn("timed out", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
