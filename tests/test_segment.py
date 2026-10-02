"""Segmentation, masking and STE word count (rules 8.4-8.7)."""
import pytest

from checker import count_words, decode_bytes, mask, segment_text


def kinds(text: str, **kw) -> list[tuple[str, str]]:
    return [(s.kind, s.text) for s in segment_text(text, **kw)]


# -- masking ------------------------------------------------------------------

def test_mask_keeps_length_and_blanks_markup():
    src = "Use `kubectl get pods` and **see** [the docs](https://x.io/a) <br> &amp; ok"
    out = mask(src)
    assert len(out) == len(src)
    assert "kubectl" not in out and "https" not in out and "<br>" not in out
    assert "see" in out and "the docs" in out and "**" not in out


def test_mask_snake_case_is_not_emphasis():
    assert mask("set max_retries now") == "set max_retries now"
    assert mask("an _important_ step").split() == ["an", "important", "step"]


def test_mask_bare_url_drops_trailing_period():
    out = mask("Go to https://example.com/x.")
    assert out.endswith("_.")


def test_mask_smart_quotes():
    assert mask("don’t") == "don't"


# -- blocks -------------------------------------------------------------------

def test_fences_front_matter_comments_and_indented_code_are_skipped():
    text = (
        "---\ntitle: x\n---\n"
        "Remove the cover.\n\n"
        "```bash\nrm -rf / ; e.g. this\n```\n"
        "~~~\nignored; text\n~~~\n"
        "<!--\nhidden; text\n-->\n\n"
        "    indented; code\n\n"
        "Close the valve.\n"
    )
    assert [s.text for s in segment_text(text)] == ["Remove the cover.", "Close the valve."]


def test_indented_text_inside_list_is_not_code():
    text = "1. Remove the cover.\n\n    Keep the screws.\n"
    assert [s.text for s in segment_text(text)] == ["Remove the cover.", "Keep the screws."]


def test_headings_are_titles():
    text = "# Removing the pump\n\nThe pump is heavy.\n\nSetext title\n===\n"
    assert kinds(text) == [("title", "Removing the pump"), ("desc", "The pump is heavy."),
                           ("title", "Setext title")]


def test_table_cells_and_separator():
    text = "| Part | Torque |\n|---|:--:|\n| Bolt | 20 Nm |\n"
    assert kinds(text) == [("cell", "Part"), ("cell", "Torque"), ("cell", "Bolt"),
                           ("cell", "20 Nm")]


def test_numbered_item_is_proc_and_bullets_by_verb():
    text = "Do these steps:\n\n1. The cover comes off.\n2. Clean the filter.\n\n- Fast startup\n- Remove the dust\n"
    assert kinds(text) == [("proc", "Do these steps:"), ("proc", "The cover comes off."),
                           ("proc", "Clean the filter."), ("desc", "Fast startup"),
                           ("proc", "Remove the dust")]


def test_numbered_description_list_is_desc():
    text = "1. The current goes up.\n2. The element melts.\n3. Replace the fuse.\n"
    assert [k for k, _ in kinds(text)] == ["desc", "desc", "proc"]
    # A paragraph between two lists makes two lists.
    text = "1. Open the cover.\n\nThe cover is red.\n\n1. The lamp comes on.\n"
    assert [k for k, _ in kinds(text)] == ["proc", "desc", "desc"]
    # Steps with unapproved verbs are still a procedure.
    text = ("1. Utilize the wizard.\n2. The restore is started by the operator.\n"
            "3. Disconnect the cable.\n")
    assert [k for k, _ in kinds(text)] == ["proc", "proc", "proc"]


def test_lead_in_colon_ends_sentence_in_same_block():
    segs = segment_text("Do these steps:\nOpen the valve. Close it.")
    assert [s.text for s in segs] == ["Do these steps:", "Open the valve.", "Close it."]


def test_blockquote_and_callouts():
    text = "> [!WARNING]\n> Do not touch the terminals. They have high voltage.\n\n> A quoted fact.\n"
    assert kinds(text) == [("warning", "Do not touch the terminals."),
                           ("warning", "They have high voltage."), ("desc", "A quoted fact.")]


@pytest.mark.parametrize("prefix,kind", [
    ("WARNING: ", "warning"), ("**CAUTION:** ", "caution"), ("Note: ", "note"),
    ("NOTE ", "note"), ("- WARNING: ", "warning"),
])
def test_signal_words(prefix, kind):
    segs = segment_text(prefix + "Do not touch the surface.")
    assert [(s.kind, s.text.strip("* ")) for s in segs][0][0] == kind
    assert segs[0].words == 5


def test_note_that_is_not_a_signal():
    assert kinds("Note that the pump is hot.") == [("desc", "Note that the pump is hot.")]


def test_line_numbers():
    text = "# T\n\nFirst sentence here.\nSecond line. Third\nsentence.\n"
    segs = segment_text(text)
    assert [(s.text, s.line) for s in segs] == [
        ("T", 1), ("First sentence here.", 3), ("Second line.", 4), ("Third\nsentence.", 4)]


def test_crlf_bom_and_utf16():
    assert decode_bytes("a\r\nb".encode("utf-8-sig")) == "a\nb"
    assert decode_bytes("Remove it.\r\n".encode("utf-16")) == "Remove it.\n"
    assert decode_bytes(b"caf\xe9") == "café"  # cp1252 fallback


# -- sentence split -------------------------------------------------------------

@pytest.mark.parametrize("text,n", [
    ("Use tools, e.g. Wrenches and pliers.", 1),
    ("Use tools, i.e. A wrench.", 1),
    ("See Fig. 3 for the parts.", 1),
    ("Set the value to 3.5 now. Then stop.", 2),
    ("Open config.json and edit it. Save it.", 2),
    ("Run `npm test`. Then stop.", 2),
    ("It stops. 20 seconds later it starts.", 2),
    ("Is it hot? Do not touch it!", 2),
    ("The pump stops (refer to step 4). The light comes on.", 2),
    ("Remove the cover. the end is here.", 1),
])
def test_sentence_split(text, n):
    assert len(segment_text(text)) == n


def test_condition_then_imperative_is_proc():
    assert kinds("If the light is on, stop the pump.")[0][0] == "proc"
    assert kinds("You must close the valve.")[0][0] == "proc"
    assert kinds("The valve is closed.")[0][0] == "desc"


def test_doc_type_override_and_validation():
    assert kinds("The valve is closed.", doc_type="proc")[0][0] == "proc"
    assert kinds("# Title", doc_type="proc")[0][0] == "title"
    with pytest.raises(ValueError):
        segment_text("x", doc_type="nope")


# -- word count (8.4-8.7) --------------------------------------------------------

@pytest.mark.parametrize("text,n", [
    ("Remove the cover.", 3),
    ("Tighten the bolt to 20 Nm.", 5),               # number + unit = 1
    ("Wait 5-10 minutes.", 2),
    ("Use a high-pressure hose.", 4),                # hyphenated = 1
    ("Open the valve (refer to step 4).", 4),        # parentheses = 1
    ('Push the "START / STOP" button.', 4),          # quoted = 1
    ("Open the Claude Code Settings page.", 4),      # Title Case run = 1
    ("Install version 2.4.1 of config.json.", 5),    # identifiers = 1
    ("Run `kubectl get pods -n prod` now.", 3),      # code span = 1
    ("Go to https://example.com/a/b now.", 4),       # URL = 1
    ("Do not use ERR_CONN_RESET values.", 5),
])
def test_word_count(text, n):
    assert count_words(mask(text)) == n


def test_list_marker_not_counted():
    assert segment_text("12. Remove the cover.")[0].words == 3


def test_ids_are_sequential_and_start_id():
    segs = segment_text("One. Two.", start_id=5)
    assert [s.id for s in segs] == ["s5", "s6"]
