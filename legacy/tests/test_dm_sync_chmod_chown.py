"""DM sync --chmod/--chown request options.

These mirror the mpifileutils dsync/nsync ``--chmod``/``--chown`` grammar that
was added upstream (chahwansong/mpifileutils @ ae8dee6). DMS validates the spec
structurally at request time (fast 422) and renders the flags into the dsync /
nsync command shared by ``_sync_flags``. Whether the bits/owner actually apply
depends on the POSIX identity the job runs as -- a non-privileged requester
cannot chown to another user -- which is exercised by the live verification, not
here.
"""

from __future__ import annotations

import pytest

from dms.adapters.volcano import _sync_flags
from dms.domain import _validate_data_sync_options


# --- chmod -----------------------------------------------------------------

@pytest.mark.parametrize(
    "spec",
    [
        "0750",       # bare octal, 4 digits
        "750",        # bare octal, 3 digits
        "0",          # 1 digit
        "7777",       # max (special bits)
        "2750",       # setgid dir
        "D0750",      # dir only
        "F0640",      # file only
        "D0750,F0640",  # separate dir/file
        "F0640,D0750",  # order-independent
    ],
)
def test_chmod_valid(spec):
    _validate_data_sync_options({"chmod": spec})  # must not raise


@pytest.mark.parametrize(
    "spec",
    [
        "",            # empty
        "8",           # 8 is not octal
        "0888",        # non-octal digits
        "75555",       # >4 digits
        "G750",        # bad prefix
        "D0750,D0640",  # duplicate dir
        "F1,F2",       # duplicate file
        "0750,F0640",  # bare cannot combine with F
        "D0750,0640",  # bare cannot combine with D
        "0750,",       # trailing empty token
        "xyz",         # garbage
        "0750 ",       # trailing space
    ],
)
def test_chmod_invalid(spec):
    with pytest.raises(ValueError):
        _validate_data_sync_options({"chmod": spec})


def test_chmod_wrong_type_rejected():
    with pytest.raises(ValueError):
        _validate_data_sync_options({"chmod": 750})


# --- chown -----------------------------------------------------------------

@pytest.mark.parametrize(
    "spec",
    [
        "alice",        # user only
        "alice:staff",  # user:group
        ":staff",       # group only
        "1000",         # numeric uid
        "1000:1000",    # numeric uid:gid
        "alice:1000",   # mixed
        ":1000",        # numeric group only
        "svc.account",  # dotted name
        "a-b_c",        # name charset
    ],
)
def test_chown_valid(spec):
    _validate_data_sync_options({"chown": spec})  # must not raise


@pytest.mark.parametrize(
    "spec",
    [
        "",            # empty
        ":",           # nothing set
        "alice:",      # trailing empty group
        "a:b:c",       # >1 colon
        "al ice",      # whitespace
        "alice staff",  # whitespace
        "alice:gr oup",  # whitespace in group
        "bad$user",    # shell metachar in name
    ],
)
def test_chown_invalid(spec):
    with pytest.raises(ValueError):
        _validate_data_sync_options({"chown": spec})


def test_chown_wrong_type_rejected():
    with pytest.raises(ValueError):
        _validate_data_sync_options({"chown": 1000})


# --- flag rendering (shared by dsync + nsync) ------------------------------

def test_sync_flags_render_chmod_chown():
    flags = _sync_flags({"chmod": "D0750,F0640", "chown": "alice:staff"})
    assert "--chmod" in flags and "D0750,F0640" in flags
    assert "--chown" in flags and "alice:staff" in flags


def test_sync_flags_combined_with_delete():
    flags = _sync_flags({"delete": True, "chmod": "0750", "chown": ":staff"})
    assert "--delete" in flags
    assert "--chmod" in flags and "0750" in flags
    assert "--chown" in flags and ":staff" in flags


def test_sync_flags_shell_quote_defense_in_depth():
    # Domain validation rejects unsafe values, but the renderer must still
    # shell-quote so a hypothetical bypass cannot inject into the command.
    flags = _sync_flags({"chown": "a;rm -rf /"})
    # shlex.quote wraps the value in single quotes, so the ';' cannot break out
    # of the argument and start a new command.
    assert "--chown 'a;rm -rf /'" in flags
