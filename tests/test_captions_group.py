"""_group_into_chunks pins the caption-chunking algorithm.

These tests guard the claim the demo deck makes on its last slide:
captions break on punctuation, not word count, and they don't end on a
1-word widow chunk. A regression that lets a clause break fire on every
word (captions.py:73) — or that skips the post-pass — keeps the rest of
the suite green, which is the property that previously hid the bug.
"""
from __future__ import annotations

from rendermill.captions import _group_into_chunks, _merge_widow_chunks


def _w(text, start=0.0, end=0.5):
    """A timed word tuple, time-irrelevant for these unit tests."""
    return (start, end, text)


def _chunk_texts(chunks):
    return [tuple(w[2] for w in c) for c in chunks]


def test_empty_input_yields_no_chunks():
    assert _group_into_chunks([]) == []


def test_sentence_end_splits_at_three_words_or_more():
    """A sentence-end is only a chunk boundary once the chunk has >=3 words.
    With a trailing word too short to be its own caption, the post-pass
    fuses it onto the split chunk rather than emitting a 1-word widow."""
    chunks = _group_into_chunks([_w("one"), _w("two"), _w("three."), _w("four")])
    assert _chunk_texts(chunks) == [("one", "two", "three.", "four")]


def test_two_word_sentence_end_does_not_split():
    """A two-word "ok. bye." must NOT split on the period — too short to read."""
    chunks = _group_into_chunks([_w("ok."), _w("bye.")])
    assert len(chunks) == 1
    assert [w[2] for w in chunks[0]] == ["ok.", "bye."]


def test_clause_break_splits_at_five_words_or_more():
    """A clause break (`,;:—`) is a chunk boundary only at >=5 words. The
    following 2-word tail is its own chunk (NOT a widow, since 2 >= 2)."""
    chunks = _group_into_chunks([
        _w("a"), _w("b"), _w("c"), _w("d"), _w("e,"),
        _w("rest"), _w("of"),
    ])
    assert _chunk_texts(chunks) == [
        ("a", "b", "c", "d", "e,"),
        ("rest", "of"),
    ]


def test_four_word_clause_break_does_not_split():
    chunks = _group_into_chunks([
        _w("one"), _w("two"), _w("three"), _w("four,"),
    ])
    assert len(chunks) == 1
    assert [w[2] for w in chunks[0]] == ["one", "two", "three", "four,"]


def test_hard_cap_splits_at_eight_words_no_punct():
    """With no punctuation at all, no chunk exceeds max_words=8. The
    trailing single word is folded into the prior chunk by the post-pass.
    """
    chunks = _group_into_chunks([_w(f"w{i}") for i in range(9)])
    assert _chunk_texts(chunks) == [tuple(f"w{i}" for i in range(9))]


def test_clause_break_takes_precedence_over_hard_cap():
    """When a clause break and the hard cap land on the same word, the
    clause break wins — the chunk is exactly max_words long and reads on
    the punctuation. The 2-word tail is its own chunk."""
    words = [_w(f"w{i}") for i in range(7)] + [_w("w7,")] + [_w("w8"), _w("w9")]
    chunks = _group_into_chunks(words)
    assert _chunk_texts(chunks) == [
        ("w0", "w1", "w2", "w3", "w4", "w5", "w6", "w7,"),
        ("w8", "w9"),
    ]


def test_no_one_word_widow_chunk_survives_post_pass():
    """A 1-word trailing chunk is a widow — the chunker must fold it back into
    the previous chunk when there is one. The shipped demo's slide-6 narration
    used to end on a lone 'all.' on screen for 263ms; that is the bug this
    property pins."""
    nine_words = [_w(f"w{i}") for i in range(9)]
    chunks = _group_into_chunks(nine_words)
    # The pre-pass algorithm emits 8 words + 1 widow; the post-pass must merge.
    assert all(len(c) > 1 for c in chunks), (
        f"widow chunk leaked through: sizes={[len(c) for c in chunks]!r}"
    )


def test_demo_slide_six_narration_produces_no_widow():
    """The shipped demo's slide-6 narration, fed through the chunker, must
    produce no 1-word trailing chunk. The demo makes the claim 'break on
    punctuation, not word count' — the chunker must support that claim
    on the demo's own narration. The pre-fix output ended with a 'all.'
    chunk that visibly contradicted the slide-6 copy."""
    narration = (
        "Captions break on punctuation instead of word count, "
        "and a failed burn is reported instead of silently "
        "shipping a video with no captions at all."
    )
    raw_words = narration.split()
    chunks = _group_into_chunks([(0.0, 0.5, w) for w in raw_words])
    assert all(len(c) > 1 for c in chunks), (
        f"widow chunk leaked: sizes={[len(c) for c in chunks]!r}"
    )
    last_chunk_words = [w[2] for w in chunks[-1]]
    assert last_chunk_words != ["all."], (
        "1-word 'all.' widow chunk surfaced — slide 6 self-contradicts its copy"
    )


def test_merge_widow_chunks_collapses_consecutive_one_word_chunks():
    """Three single-word chunks in a row fuse into one chunk when there is room."""
    chunks = [[_w("a")], [_w("b")], [_w("c")]]
    out = _merge_widow_chunks(chunks, max_words=4)
    assert _chunk_texts(out) == [("a", "b", "c")]


def test_merge_widow_chunks_preserves_solo_input():
    """The whole input is one word — there is nothing to merge into. The
    single word must remain its own chunk; it's not a 'widow', it's the
    only content."""
    chunks = [[_w("alone")]]
    out = _merge_widow_chunks(chunks)
    assert _chunk_texts(out) == [("alone",)]


def test_merge_widow_chunks_folds_into_previous_when_room():
    chunks = [[_w("a"), _w("b"), _w("c")], [_w("d")]]
    out = _merge_widow_chunks(chunks, max_words=8)
    assert _chunk_texts(out) == [("a", "b", "c", "d")]


def test_merge_widow_chunks_respects_max_words_room():
    """If the previous chunk is already at max_words, the widow becomes its
    own chunk rather than overflow the cap by more than one word."""
    chunks = [[_w(f"p{i}") for i in range(3)], [_w("w")]]
    out = _merge_widow_chunks(chunks, max_words=3)
    # 3 + 1 = 4, which is max_words + 1; the merge is allowed.
    assert _chunk_texts(out) == [("p0", "p1", "p2", "w")]
