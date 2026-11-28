import time

from logpipe.parsing.kv import compile_extractor, default_keys, extract, slot_labels

TEMPLATE = [
    "<VAR:TIMESTAMP>",
    "[<VAR:LOGLEVEL>]",
    "SN:TH-<VAR:NUMBER>",
    "temp=<VAR:MEASURE>",
    "<*>",
    "<*>",
]


def test_slot_labels_and_default_keys():
    assert slot_labels(TEMPLATE) == ["TIMESTAMP", "LOGLEVEL", "NUMBER", "MEASURE", None, None]
    assert default_keys(TEMPLATE) == ["timestamp", "loglevel", "number", "temp", "var", "var_1"]


def test_extract_handles_multi_word_mask_and_inline_key_value():
    pattern = compile_extractor(TEMPLATE)
    line = "2026-04-13 10:15:23.123 [INFO] SN:TH-45678 temp=22.5C mode=auto 17"
    assert extract(pattern, default_keys(TEMPLATE), line) == {
        "timestamp": "2026-04-13 10:15:23.123",
        "loglevel": "INFO",
        "number": "45678",
        "temp": "22.5C",
        "mode": "auto",
        "var_1": "17",
    }


def test_extract_rejects_lines_that_do_not_fit():
    pattern = compile_extractor(["Door", "sensor", "contact=<*>"])
    assert extract(pattern, ["var"], "Window sensor contact=open") is None


def test_literals_are_escaped():
    pattern = compile_extractor(["a.b", "(x)", "<*>"])
    assert extract(pattern, ["v"], "a.b (x) 1") == {"v": "1"}
    assert extract(pattern, ["v"], "aXb (x) 1") is None


def test_a_line_that_backtracks_gives_up_instead_of_hanging():
    # Train Ticket's ticket lists: fails only at the last <*>, after trying every split of the rest
    ticket = "Ticket(seatNo=<VAR:MEASURE>, startStation=shanghai, destStation=nanjing),"
    tokens = ["<VAR:TIMESTAMP>", "Left", "ticket", "info"] + ticket.split() * 30
    tokens[-1] = "destStation=nanjing)]}<*>"
    line = "2024-12-07 20:09:02 Left ticket info " + " ".join(
        f"Ticket(seatNo={i}, startStation=shanghai, destStation=nanjing)," for i in range(30)
    )
    start = time.monotonic()
    assert extract(compile_extractor(tokens), default_keys(tokens), line[:-1] + ")]}") is None
    assert time.monotonic() - start < 2
