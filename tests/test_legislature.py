"""Tests for the Texas Legislature collector (fictional journal text in the journals' own layout)."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "collectors" / "states" / "tx"))
import legislature as L  # noqa: E402


def row(chamber, action, date, comment="", journal="89RDAY46FINAL.PDF#page=12", page="1670"):
    link = f'<a href="http://journals.house.texas.gov/hjrnl/89r/pdf/{journal}">{action}</a>' if journal else action
    return (f'<tr><td data-label="Action Chamber">{chamber}&nbsp;</td>\n<td data-label="Action Description">&nbsp;{link}&nbsp;</td>\n'
            f'<td data-label="Action Comment" class="mobile">&nbsp;{comment}&nbsp;</td>\n<td data-label="Action Date">&nbsp;{date}&nbsp;</td>\n'
            f'<td data-label="Action Time">&nbsp;&nbsp;</td>\n<td data-label="Journal Page Number" class="mobile">{page}</td></tr>')


HISTORY = ('<span id="lblCaptionText">Relating to a test.</span>' + "".join([  # newest first, as TLO lists them
    row("E", "Signed by the Governor", "06/20/2025", journal=None, page=""),
    row("H", "Record vote", "05/29/2025", "RV#4015"),
    row("H", "House concurs in Senate amendment(s)", "05/29/2025"),
    row("S", "Record vote", "05/23/2025", journal="89RSJ05-23-F.PDF#page=43"),
    row("S", "Passed as amended", "05/23/2025", journal="89RSJ05-23-F.PDF#page=43"),
    row("H", "Record vote", "04/17/2025", "RV#303"),
    row("H", "Passed", "04/17/2025"),
    row("H", "Record vote", "04/16/2025", "RV#240"),
    row("H", "Passed to engrossment as amended", "04/16/2025"),
]))


def test_final_votes_are_passage_and_concurrence_with_their_record_numbers():
    h = L.history(HISTORY)
    assert h["caption"] == "Relating to a test."
    got = [(v["chamber"], v["what"], v["record"], v["record_vote"]) for v in L.final_votes(h["actions"])]
    assert got == [("H", "passed", 303, True), ("S", "passed as amended", None, True),
                   ("H", "concurred in the Senate's amendments", 4015, True)]  # engrossment isn't final passage


def test_a_house_roll_call_is_read_name_by_name_across_a_page_header():
    text = L.journal_text(
        "HBi2 was passed by (Record 303): 3 Yeas, 1 Nays, 0 Present, not voting.\n"
        "Yeas — Mr. Speaker(C); Bell, C.; Garcia\n"
        "1670 89th LEGISLATURE — REGULAR SESSIONHernandez.\nNays — Cain.\nAbsent, Excused — Jones, V.\nAbsent — Kerwin.\n"
        "STATEMENTS OF VOTE\n")
    rc = L.house_roll_call(text, 303)
    assert rc["problem"] is None
    assert rc["names"] == {"yeas": ["Mr. Speaker(C)", "Bell, C.", "Garcia Hernandez"], "nays": ["Cain"],
                           "absent_excused": ["Jones, V."], "absent": ["Kerwin"]}


def test_a_house_tally_that_doesnt_match_its_names_is_refused():
    text = "HB 9 was passed by (Record 7): 3 Yeas, 0 Nays, 0 Present, not voting.\nYeas — Allen; Bell, C..\nSTATEMENTS OF VOTE"
    assert L.house_roll_call(text, 7)["names"] is None


def test_a_senate_vote_is_found_by_its_bill_and_kind():
    text = L.journal_text("CSHB 2 as amended was finally passed by the following vote:iiYeasi31, Naysi0.\n"
                          "SB 5 was passed by the following vote: Yeas 20, Nays 2. Nays: Hall, Hughes.\n")
    rc = L.senate_roll_call(text, "HB 2", "passed as amended")
    assert rc["tally"] == {"yeas": 31, "nays": 0} and rc["names"] is None
    assert rc["quote"] == "CSHB 2 as amended was finally passed by the following vote: Yeas 31, Nays 0."
    rc = L.senate_roll_call(text, "SB 5", "passed")
    assert rc["names"] == {"nays": ["Hall", "Hughes"]} and rc["tally"] == {"yeas": 20, "nays": 2}
    assert L.senate_roll_call(text, "SB 50", "passed") is None  # SB 5 is not SB 50


def test_a_senate_heading_vote_skips_the_motion_to_suspend_the_rules():
    text = L.journal_text(
        "COMMITTEE SUBSTITUTE\nHOUSE BILL 143 ON THIRD READING\nSenator Hancock moved that Senate Rule 7.18 be suspended.\n"
        "The motion prevailed by the following vote: Yeas 30, Nays 1.\nFriday, May 23, 2025 SENATE JOURNAL 2489\n"
        "The bill was read third time and was passed by the following vote: Yeas 31, Nays 0.\n"
        "HOUSE BILL 171 ON THIRD READING\nThe bill was read third time and was passed by the following vote: Yeas 29, Nays 2.\n")
    rc = L.senate_roll_call(text, "HB 143", "passed")
    assert rc["tally"] == {"yeas": 31, "nays": 0}
    assert rc["quote"] == "The bill was read third time and was passed by the following vote: Yeas 31, Nays 0."


def test_journal_names_match_members_by_surname_and_initial():
    pool = [{"list_name": "Bell, Cecil"}, {"list_name": "Bell, Keith"}, {"list_name": "Garcia Hernandez, Cassandra"},
            {"list_name": "Anchía"}]
    assert L.printed_match("Bell, C.", pool) == [{"list_name": "Bell, Cecil"}]
    assert L.printed_match("Garcia Hernandez", pool) == [{"list_name": "Garcia Hernandez, Cassandra"}]
    assert L.printed_match("Anchia", pool) == [{"list_name": "Anchía"}]
    assert len(L.printed_match("Bell", pool)) == 2  # ambiguous: never guessed


def test_a_house_roll_call_reads_when_the_pdf_dropped_its_spaces():
    text = L.journal_text("HBi9 was passed by\n(Record4015):2Yeas,1Nays,0Present,notvoting.\nYeas — Allen; Bell, C.\nNays — Cain.\n"
                          "STATEMENTS OF VOTE\n")
    rc = L.house_roll_call(text, 4015)
    assert rc["problem"] is None and rc["names"] == {"yeas": ["Allen", "Bell, C."], "nays": ["Cain"]}
    assert rc["quote"] == "HB 9 was passed by (Record 4015): 2 Yeas, 1 Nays, 0 Present, not voting."  # spacing restored


BLOCK = ("were laid before the house, read third time, and passed by the following record vote\n(members registering votes "
         "and the results of the vote are shown following the bill number).\n(Record 552): 3 Yeas, 1 Nays, 0 Present, not voting.\n"
         "Yeas — Allen; Bell, K.; Garcia.\nNays — Cain.\nSTATEMENTS OF VOTE\nWhen Record No. 552 was taken, I was away.\nAllen\n"
         "HB 77 (Allen and K. Belli-ino; Caini-iyes) (2i-i2i-i0)\nHB 78\nHB 790 (Garciai-ino) (2i-i2i-i0)\n")


def test_a_calendar_bill_applies_its_listed_changes_to_the_roll_call():
    text = L.journal_text(BLOCK)
    assert L.house_roll_call(text, 552)["block"]
    rc = L.house_block_vote(text, 552, "HB 77")
    assert rc["problem"] is None and rc["tally"] == {"yeas": 2, "nays": 2, "present_not_voting": 0}
    assert rc["names"] == {"yeas": ["Garcia", "Cain"], "nays": ["Allen", "Bell, K."]}
    same = L.house_block_vote(text, 552, "HB 78")  # alone on its line: the roll call as printed
    assert same["names"] == {"yeas": ["Allen", "Bell, K.", "Garcia"], "nays": ["Cain"]} and same["quote"] == "HB 78"
    assert L.house_block_vote(text, 552, "HB 79") is None  # HB 790 is not HB 79


def test_a_calendar_bill_whose_changes_dont_add_up_is_refused():
    text = L.journal_text(BLOCK.replace("(2i-i2i-i0)\nHB 78", "(3i-i1i-i0)\nHB 78"))
    assert L.house_block_vote(text, 552, "HB 77")["names"] is None


def test_a_senate_vote_same_as_the_previous_roll_call_takes_its_names():
    text = L.journal_text(
        "HOUSE BILL 1105 ON THIRD READING\nSenator Eckhardt moved that Senate Rule 7.18 be suspended.\n"
        "Themotionprevailedbythefollowingvote: Yeas 3,Nays 2.\n2832 89th Legislature — Regular Session 45th Day (Cont.)\n"
        "Yeas: Alvarado, A. Hinojosa, J. Hinojosa.\nNays: Campbell,Hughes.\n"
        "The bill was read third time and was passed by the following vote: Yeas 3,\nNays 2.(Sameas previousrollcall)\n")
    rc = L.senate_roll_call(text, "HB 1105", "passed")
    assert rc["names"]["nays"] == ["Campbell", "Hughes"]
    assert rc["quote"].endswith("Yeas 3, Nays 2. (Same as previous roll call)")


def test_a_senate_local_calendar_vote_is_the_last_tally_after_the_caption():
    text = L.journal_text(
        "the votes on passage to engrossment or third reading, suspension of the Constitutional Three-day\nRule, and final "
        'passage are indicated after each caption. All Members are deemed to\nhavevoted"Yea"onvivavocevotesunlessotherwiseindicated.\n'
        "CSSB 1538(Zaffirini)\nRelating to a study.\n(vivavocevote)(31-0)(31-0)\n"
        'SB 3038(Huffman)\nRelating to a district.\n(viva voce vote) (27-4) "Nays" Hall, Hughes, Middleton, Sparks (27-4) "Nays" Hall,\n'
        "Hughes,Middleton,Sparks\nHB 2259(Zaffirini)\nRelating to a form.\n(vivavocevote)(31-0)(31-0)\n")
    rc = L.senate_calendar_vote(text, "SB 3038")
    assert rc["tally"] == {"yeas": 27, "nays": 4} and rc["names"] == {"nays": ["Hall", "Hughes", "Middleton", "Sparks"]}
    assert L.senate_calendar_vote(text, "HB 2259")["tally"] == {"yeas": 31, "nays": 0}
    assert L.senate_calendar_vote(text, "SB 1538")["tally"] == {"yeas": 31, "nays": 0}
    assert L.senate_calendar_vote(text, "SB 303") is None


def test_a_heading_inside_the_senate_calendar_doesnt_end_it():
    text = L.journal_text(
        'All Members are deemed to\nhave voted "Yea" on viva voce votes unless otherwise indicated.\nORDERED NOT PRINTED\n'
        "On motion of Senator Hall and by unanimous consent, HB 1610 was ordered not printed.\n"
        "HB 37 (Huffman)\nRelating to care.\n(viva voce vote) (31-0) (31-0)\n"
        'HB 1238 (Parker)\nRelating to a well.\n(viva voce vote) (29-2) "Nays" Cook, Eckhardt (29-2) "Nays" Cook,\n'
        "Eckhardt (Senator Hall in Chair)\nCO-SPONSORS OF HOUSE BILL 37\nOn motion of Senator Huffman, Senator West will be shown as\n"
        "Co-sponsor of HB 37.\n")
    assert L.senate_calendar_vote(text, "HB 37")["tally"] == {"yeas": 31, "nays": 0}
    assert L.senate_calendar_vote(text, "HB 1238")["names"] == {"nays": ["Cook", "Eckhardt"]}  # who presided isn't a vote


def test_a_senate_heading_that_lost_its_spaces_still_finds_the_vote():
    text = L.journal_text(
        "CONFERENCECOMMITTEEREPORTON\nSENATEBILL763ADOPTED\nSenator Alvarado called from the President's table the Conference "
        "Committee\nReport on SB 763.\nOn motion of Senator Alvarado, the Conference Committee Report was adopted\n"
        "by the following vote: Yeas 31, Nays 0.\n(PresidentinChair)\nCONFERENCECOMMITTEEREPORTON\nHOUSEBILL2067ADOPTED\n")
    rc = L.senate_roll_call(text, "SB 763", "adopted the conference committee report")
    assert rc["tally"] == {"yeas": 31, "nays": 0} and not rc["problem"]
    assert L.senate_roll_call(text, "SB 76", "adopted the conference committee report") is None


def test_journal_names_that_lost_spaces_or_carry_marks_still_match():
    pool = [{"list_name": "Garcia Hernandez, Cassandra"}, {"list_name": "Hinojosa, Adam"}, {"list_name": "Hinojosa, Juan \"Chuy\""},
            {"list_name": "Landgraf, Brooks"}]
    assert L.printed_match("GarciaHernandez", pool) == [pool[0]]
    assert L.printed_match("A. Hinojosa", pool) == [pool[1]]  # the Senate prints the initial first
    assert L.printed_match("J. Hinojosa", pool) == [pool[2]]
    assert L.printed_match("Landgraf(C)", pool) == [pool[3]]  # in the chair
    assert L.senator("A.Hinojosa") == "A. Hinojosa"  # spaced as the Senate roll prints it
