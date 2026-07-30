"""The wire protocol. Both processes import this module, so a break here breaks both."""

import io

import pytest

from core import protocol as p


def _round_trip_command(msg: p.Command) -> p.Command:
    return p.decode_command(msg.to_dict())


def _round_trip_event(msg: p.Event) -> p.Event:
    return p.decode_event(msg.to_dict())


# --------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------


def test_hello_round_trip() -> None:
    assert _round_trip_command(p.Hello(variant="llvm_ad_rgb")) == p.Hello(
        protocol=p.PROTOCOL_VERSION, variant="llvm_ad_rgb"
    )


def test_render_round_trip() -> None:
    msg = p.Render(job=7, scene={"type": "scene"}, shm="C:/tmp/f.film",
                   width=640, height=480, spp_per_pass=8, passes=4, seed=99,
                   scene_root="C:/tmp/export")
    assert _round_trip_command(msg) == msg


def test_render_film_is_nested_as_the_spec_says() -> None:
    """SPEC 10 writes the film size as `{"film": {"w":.., "h":..}}`, not flat keys."""
    d = p.Render(job=1, scene={}, shm="x", width=3, height=4).to_dict()
    assert d["film"] == {"w": 3, "h": 4}


def test_cancel_and_shutdown_round_trip() -> None:
    assert _round_trip_command(p.Cancel(job=3)) == p.Cancel(job=3)
    assert _round_trip_command(p.Shutdown()) == p.Shutdown()


def test_unknown_command_raises() -> None:
    with pytest.raises(p.ProtocolError, match="unknown command"):
        p.decode_command({"cmd": "explode"})


# --------------------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------------------


def test_ready_round_trip() -> None:
    msg = p.Ready(mitsuba="3.9.0", variant="cuda_ad_rgb", python="3.13.9",
                  available_variants=("scalar_rgb", "cuda_ad_rgb"))
    assert _round_trip_event(msg) == msg


def test_pass_done_error_round_trip() -> None:
    for msg in (
        p.PassEv(job=1, index=4, spp_done=64, elapsed_s=1.25),
        p.Done(job=1, spp_done=512, elapsed_s=12.5, cancelled=True),
        p.ErrorEv(message="boom", job=1, traceback="Traceback..."),
    ):
        assert _round_trip_event(msg) == msg


def test_error_without_a_job_round_trips() -> None:
    """A crash during startup has no job number. `None` must survive, not become 0."""
    msg = p.ErrorEv(message="startup failed")
    back = _round_trip_event(msg)
    assert isinstance(back, p.ErrorEv)
    assert back.job is None


def test_unknown_event_raises() -> None:
    with pytest.raises(p.ProtocolError, match="unknown event"):
        p.decode_event({"ev": "vibes"})


# --------------------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------------------


def test_write_message_is_one_line_and_flushed() -> None:
    buf = io.StringIO()
    p.write_message(buf, p.Cancel(job=2))
    text = buf.getvalue()
    assert text.endswith("\n")
    assert text.count("\n") == 1


def test_write_message_never_emits_embedded_newlines() -> None:
    """A traceback contains newlines and would otherwise split into several messages,
    each of which is invalid JSON."""
    buf = io.StringIO()
    p.write_message(buf, p.ErrorEv(message="a\nb", traceback="x\ny\nz"))
    assert buf.getvalue().count("\n") == 1


def test_read_messages_skips_blank_lines() -> None:
    stream = io.StringIO('{"ev":"log","message":"a"}\n\n{"ev":"log","message":"b"}\n')
    msgs = list(p.read_messages(stream))
    assert [m["message"] for m in msgs] == ["a", "b"]


def test_read_messages_rejects_garbage() -> None:
    """Mitsuba logging leaking onto stdout is the realistic cause, and the error must say
    what it saw rather than raising a bare JSONDecodeError three frames deep."""
    with pytest.raises(p.ProtocolError, match="malformed message"):
        list(p.read_messages(io.StringIO("jitc_llvm_init(): LLVM API init failed\n")))


def test_read_messages_rejects_a_bare_array() -> None:
    with pytest.raises(p.ProtocolError, match="expected a JSON object"):
        list(p.read_messages(io.StringIO("[1, 2, 3]\n")))


def test_round_trip_through_the_transport() -> None:
    buf = io.StringIO()
    sent: list[p.Event] = [
        p.Ready(mitsuba="3.9.0", variant="cuda_ad_rgb", python="3.13.9"),
        p.PassEv(job=1, index=1, spp_done=16, elapsed_s=0.5),
        p.Done(job=1, spp_done=16, elapsed_s=0.6),
    ]
    for msg in sent:
        p.write_message(buf, msg)
    buf.seek(0)
    assert [p.decode_event(d) for d in p.read_messages(buf)] == sent
