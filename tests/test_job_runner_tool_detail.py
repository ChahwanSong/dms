"""summary v2 상세(parsers.parse_tool_detail)·실패 사유(runner._derive_reason)·사유 마커와 도구 출력 꼬리(2026-10-27 d192).

픽스처는 현재 포크 빌드(mpifileutils 7a3189c)를 개발 컨테이너에서 돌린 **실제 출력**을 글자 그대로 옮긴 것이다(mfu 하네스
samples.sh -- 각 상수는 `<도구> ... >name.out 2>&1` 의 내용, *_RC 는 종료 코드). 2>&1 이라 mpirun 배너("Primary job
terminated normally ...")가 끝에 섞여 있다 -- 실제 러너에서는 stderr 로 따로 온다(_split). 파서가 「실제 도구가 찍는 것」을
읽는다는 것을 픽스처 수준에서 보증한다(설계 §6). 위조 줄·중단(ABORT)·ENOSPC 처럼 표본으로 만들기 어려운 모양만 합성이고,
합성은 그렇다고 적는다."""
import io
import json

import pytest

from dms_job_runner import parsers, runner
from dms_job_runner.parsers import parse_tool_detail
from dms_job_runner.runner import _build_summary, _derive_reason, run_job

# ---- 실제 출력(포크 7a3189c, 2026-10-27 samples.sh) ----

DRM_EXEC_OK_RC = 0
DRM_EXEC_OK = """\
[2026-10-27T13:52:58] Walking /tmp/smp/rm2
[2026-10-27T13:52:58] Walked 3 items in 0.001 secs (4580.733 items/sec) ...
[2026-10-27T13:52:58] Walked 3 items in 0.001 seconds (3221.099 items/sec)
[2026-10-27T13:52:58] Removing 3 items
[2026-10-27T13:52:58] Removed 3 items in 0.001 seconds (4094.183 items/sec)
"""

DRM_EXEC_PARTIAL_RC = 1
DRM_EXEC_PARTIAL = """\
[2026-10-27T13:52:56] Walking /tmp/smp/rm/t
[2026-10-27T13:52:56] Walked 9 items in 0.001 secs (14201.788 items/sec) ...
[2026-10-27T13:52:56] Walked 9 items in 0.001 seconds (9975.615 items/sec)
[2026-10-27T13:52:56] Removing 9 items
[2026-10-27T13:52:56] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:84] ERROR: Failed to unlink `/tmp/smp/rm/t/root/r2' (errno=13 Permission denied)
[2026-10-27T13:52:56] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:84] ERROR: Failed to unlink `/tmp/smp/rm/t/root/r1' (errno=13 Permission denied)
[2026-10-27T13:52:56] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:84] ERROR: Failed to unlink `/tmp/smp/rm/t/root/r3' (errno=13 Permission denied)
[2026-10-27T13:52:56] [1] [/release/mpifileutils/src/common/mfu_flist_remove.c:76] ERROR: Failed to rmdir `/tmp/smp/rm/t/root' (errno=39 Directory not empty)
[2026-10-27T13:52:56] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:76] ERROR: Failed to rmdir `/tmp/smp/rm/t' (errno=13 Permission denied)
[2026-10-27T13:52:56] Removed 4 items in 0.001 seconds (3975.621 items/sec)
[2026-10-27T13:52:56] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:761] ERROR: Remove failed for 5 of 9 items
--------------------------------------------------------------------------
Primary job  terminated normally, but 1 process returned
a non-zero exit code. Per user-direction, the job has been aborted.
--------------------------------------------------------------------------
--------------------------------------------------------------------------
mpirun detected that one or more processes exited with non-zero status, thus causing
the job to be terminated. The first process to do so was:

  Process name: [[12197,1],0]
  Exit code:    1
--------------------------------------------------------------------------
"""

DSYNC_EXEC_DELETE_OK_RC = 0
DSYNC_EXEC_DELETE_OK = """\
[2026-10-27T13:52:53] Walking source path
[2026-10-27T13:52:53] Walking /tmp/smp/ds/src
[2026-10-27T13:52:53] Walked 6 items in 0.001 secs (9700.293 items/sec) ...
[2026-10-27T13:52:53] Walked 6 items in 0.001 seconds (8302.614 items/sec)
[2026-10-27T13:52:53] Walking destination path
[2026-10-27T13:52:53] Walking /tmp/smp/ds/dst
[2026-10-27T13:52:53] Walked 7 items in 0.000 secs (20747.257 items/sec) ...
[2026-10-27T13:52:53] Walked 7 items in 0.000 seconds (19283.853 items/sec)
[2026-10-27T13:52:53] Comparing file sizes and modification times of 2 items
[2026-10-27T13:52:53] Started   : Oct-27-2026, 13:52:53
[2026-10-27T13:52:53] Completed : Oct-27-2026, 13:52:53
[2026-10-27T13:52:53] Seconds   : 0.000
[2026-10-27T13:52:53] Items     : 2
[2026-10-27T13:52:53] Item Rate : 2 items in 0.000219 seconds (9117.474095 items/sec)
[2026-10-27T13:52:53] Deleting items from destination
[2026-10-27T13:52:53] Removing 3 items
[2026-10-27T13:52:53] Removed 3 items in 0.001 seconds (5478.021 items/sec)
[2026-10-27T13:52:53] Copying items to destination
[2026-10-27T13:52:53] Copying to /tmp/smp/ds/dst
[2026-10-27T13:52:53] Items: 2
[2026-10-27T13:52:53]   Directories: 0
[2026-10-27T13:52:53]   Files: 1
[2026-10-27T13:52:53]   Links: 0
[2026-10-27T13:52:53] Data: 2.000 B (2.000 B per file)
[2026-10-27T13:52:53] Skipped 1 special file(s) in the source (FIFO, socket or device): not copied
[2026-10-27T13:52:53] Creating 1 files.
[2026-10-27T13:52:53] Copying data.
[2026-10-27T13:52:53] Copy data: 2.000 B (2 bytes)
[2026-10-27T13:52:53] Copy rate: 258.729 B/s (2 bytes in 0.008 seconds)
[2026-10-27T13:52:53] Syncing data to disk.
[2026-10-27T13:52:53] Sync completed in 0.017 seconds.
[2026-10-27T13:52:53] Setting ownership, permissions, and timestamps.
[2026-10-27T13:52:53] Updated 1 items in 0.000 seconds (4315.777 items/sec)
[2026-10-27T13:52:53] Syncing directory updates to disk.
[2026-10-27T13:52:53] Sync completed in 0.009 seconds.
[2026-10-27T13:52:53] Started: Oct-27-2026,13:52:53
[2026-10-27T13:52:53] Completed: Oct-27-2026,13:52:53
[2026-10-27T13:52:53] Seconds: 0.034
[2026-10-27T13:52:53] Items: 1
[2026-10-27T13:52:53]   Directories: 0
[2026-10-27T13:52:53]   Files: 1
[2026-10-27T13:52:53]   Links: 0
[2026-10-27T13:52:53] Data: 2.000 B (2 bytes)
[2026-10-27T13:52:53] Rate: 58.571 B/s (002 bytes in 0.034 seconds)
[2026-10-27T13:52:53] Updating timestamps on newly copied files
[2026-10-27T13:52:53] Completed updating timestamps
[2026-10-27T13:52:53] Completed sync
"""

DSYNC_EXEC_DIRECT_BAD_BUFSIZE_RC = 0
DSYNC_EXEC_DIRECT_BAD_BUFSIZE = """\
[2026-10-27T13:52:58] Using O_DIRECT
[2026-10-27T13:52:58] Walking source path
[2026-10-27T13:52:58] Walking /tmp/smp/dir/src
[2026-10-27T13:52:58] Walked 2 items in 0.001 secs (1749.731 items/sec) ...
[2026-10-27T13:52:58] Walked 2 items in 0.001 seconds (1361.933 items/sec)
[2026-10-27T13:52:58] Walking destination path
[2026-10-27T13:52:58] Walking /tmp/smp/dir/dst
[2026-10-27T13:52:58] Walked 1 items in 0.000 secs (2206.541 items/sec) ...
[2026-10-27T13:52:58] Walked 1 items in 0.000 seconds (2096.230 items/sec)
[2026-10-27T13:52:58] Started   : Oct-27-2026, 13:52:58
[2026-10-27T13:52:58] Completed : Oct-27-2026, 13:52:58
[2026-10-27T13:52:58] Seconds   : 0.000
[2026-10-27T13:52:58] Items     : 0
[2026-10-27T13:52:58] Item Rate : 0 items in 0.000165 seconds (0.000000 items/sec)
[2026-10-27T13:52:58] Copying items to destination
[2026-10-27T13:52:58] Copying to /tmp/smp/dir/dst
[2026-10-27T13:52:58] Items: 1
[2026-10-27T13:52:58]   Directories: 0
[2026-10-27T13:52:58]   Files: 1
[2026-10-27T13:52:58]   Links: 0
[2026-10-27T13:52:58] Data: 97.656 KiB (97.656 KiB per file)
[2026-10-27T13:52:58] Creating 1 files.
[2026-10-27T13:52:58] Copying data.
[2026-10-27T13:52:58] Copy data: 97.656 KiB (100000 bytes)
[2026-10-27T13:52:58] Copy rate: 4.478 MiB/s (100000 bytes in 0.021 seconds)
[2026-10-27T13:52:58] Syncing data to disk.
[2026-10-27T13:52:58] Sync completed in 0.015 seconds.
[2026-10-27T13:52:58] Setting ownership, permissions, and timestamps.
[2026-10-27T13:52:58] Updated 1 items in 0.000 seconds (11454.229 items/sec)
[2026-10-27T13:52:58] Syncing directory updates to disk.
[2026-10-27T13:52:58] Sync completed in 0.016 seconds.
[2026-10-27T13:52:58] Started: Oct-27-2026,13:52:58
[2026-10-27T13:52:58] Completed: Oct-27-2026,13:52:58
[2026-10-27T13:52:58] Seconds: 0.053
[2026-10-27T13:52:58] Items: 1
[2026-10-27T13:52:58]   Directories: 0
[2026-10-27T13:52:58]   Files: 1
[2026-10-27T13:52:58]   Links: 0
[2026-10-27T13:52:58] Data: 97.656 KiB (100000 bytes)
[2026-10-27T13:52:58] Rate: 1.788 MiB/s (100000 bytes in 0.053 seconds)
[2026-10-27T13:52:58] Updating timestamps on newly copied files
[2026-10-27T13:52:58] Completed updating timestamps
[2026-10-27T13:52:58] Completed sync
"""

DSYNC_EXEC_ITEMS_FAILED_RC = 1
DSYNC_EXEC_ITEMS_FAILED = """\
[2026-10-27T13:52:53] Walking source path
[2026-10-27T13:52:53] Walking /tmp/smp/dsf/src
[2026-10-27T13:52:53] Walked 3 items in 0.001 secs (5728.842 items/sec) ...
[2026-10-27T13:52:53] Walked 3 items in 0.001 seconds (4765.475 items/sec)
[2026-10-27T13:52:53] Walking destination path
[2026-10-27T13:52:53] Walking /tmp/smp/dsf/dst
[2026-10-27T13:52:53] Walked 1 items in 0.000 secs (4386.965 items/sec) ...
[2026-10-27T13:52:53] Walked 1 items in 0.000 seconds (3990.550 items/sec)
[2026-10-27T13:52:53] Started   : Oct-27-2026, 13:52:53
[2026-10-27T13:52:53] Completed : Oct-27-2026, 13:52:53
[2026-10-27T13:52:53] Seconds   : 0.000
[2026-10-27T13:52:53] Items     : 0
[2026-10-27T13:52:53] Item Rate : 0 items in 0.000340 seconds (0.000000 items/sec)
[2026-10-27T13:52:53] Copying items to destination
[2026-10-27T13:52:53] Copying to /tmp/smp/dsf/dst
[2026-10-27T13:52:53] Items: 2
[2026-10-27T13:52:53]   Directories: 0
[2026-10-27T13:52:53]   Files: 2
[2026-10-27T13:52:53]   Links: 0
[2026-10-27T13:52:53] Data: 6.000 B (3.000 B per file)
[2026-10-27T13:52:53] Creating 2 files.
[2026-10-27T13:52:53] Copying data.
[2026-10-27T13:52:53] [0] [/release/mpifileutils/src/common/mfu_flist_copy.c:2092] ERROR: Failed to open input file `/tmp/smp/dsf/src/no' (errno=13 Permission denied)
[2026-10-27T13:52:53] [0] [/release/mpifileutils/src/common/mfu_flist_copy.c:2256] ERROR: Failed to copy `/tmp/smp/dsf/src/no' to `/tmp/smp/dsf/dst/no'
[2026-10-27T13:52:53] Copy data: 6.000 B (6 bytes)
[2026-10-27T13:52:53] Copy rate: 703.390 B/s (6 bytes in 0.009 seconds)
[2026-10-27T13:52:53] Syncing data to disk.
[2026-10-27T13:52:53] Sync completed in 0.015 seconds.
[2026-10-27T13:52:53] Setting ownership, permissions, and timestamps.
[2026-10-27T13:52:53] Updated 2 items in 0.000 seconds (11273.068 items/sec)
[2026-10-27T13:52:53] Syncing directory updates to disk.
[2026-10-27T13:52:53] Sync completed in 0.011 seconds.
[2026-10-27T13:52:53] Started: Oct-27-2026,13:52:53
[2026-10-27T13:52:53] Completed: Oct-27-2026,13:52:53
[2026-10-27T13:52:53] Seconds: 0.036
[2026-10-27T13:52:53] Items: 2
[2026-10-27T13:52:53]   Directories: 0
[2026-10-27T13:52:53]   Files: 2
[2026-10-27T13:52:53]   Links: 0
[2026-10-27T13:52:53] Data: 3.000 B (3 bytes)
[2026-10-27T13:52:53] Rate: 84.312 B/s (003 bytes in 0.036 seconds)
[2026-10-27T13:52:53] Updating timestamps on newly copied files
[2026-10-27T13:52:53] Completed updating timestamps
[2026-10-27T13:52:53] Completed sync
--------------------------------------------------------------------------
Primary job  terminated normally, but 1 process returned
a non-zero exit code. Per user-direction, the job has been aborted.
--------------------------------------------------------------------------
--------------------------------------------------------------------------
mpirun detected that one or more processes exited with non-zero status, thus causing
the job to be terminated. The first process to do so was:

  Process name: [[12212,1],0]
  Exit code:    1
--------------------------------------------------------------------------
"""

DSYNC_EXEC_TYPE_CHANGE_RC = 1
DSYNC_EXEC_TYPE_CHANGE = """\
[2026-10-27T13:52:52] Walking source path
[2026-10-27T13:52:52] Walking /tmp/smp/ds/src
[2026-10-27T13:52:52] Walked 6 items in 0.001 secs (4823.965 items/sec) ...
[2026-10-27T13:52:52] Walked 6 items in 0.001 seconds (4139.895 items/sec)
[2026-10-27T13:52:52] Walking destination path
[2026-10-27T13:52:52] Walking /tmp/smp/ds/dst
[2026-10-27T13:52:52] Walked 4 items in 0.001 secs (5982.301 items/sec) ...
[2026-10-27T13:52:52] Walked 4 items in 0.001 seconds (5365.339 items/sec)
[2026-10-27T13:52:52] Started   : Oct-27-2026, 13:52:52
[2026-10-27T13:52:52] Completed : Oct-27-2026, 13:52:52
[2026-10-27T13:52:52] Seconds   : 0.000
[2026-10-27T13:52:52] Items     : 0
[2026-10-27T13:52:52] Item Rate : 0 items in 0.000421 seconds (0.000000 items/sec)
[2026-10-27T13:52:52] Deleting items from destination
[2026-10-27T13:52:52] Removing 1 items
[2026-10-27T13:52:52] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:76] ERROR: Failed to rmdir `/tmp/smp/ds/dst/d' (errno=39 Directory not empty)
[2026-10-27T13:52:52] Removed 0 items in 0.001 seconds (0.000 items/sec)
[2026-10-27T13:52:52] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:761] ERROR: Remove failed for 1 of 1 items
[2026-10-27T13:52:52] [1] [/release/mpifileutils/src/dsync/dsync.c:1724] ERROR: Type change not applied: `/tmp/smp/ds/dst/d' could not be removed (still a directory), a non-empty directory is replaced only with --delete; source item not copied or linked
[2026-10-27T13:52:52] [0] [/release/mpifileutils/src/dsync/dsync.c:1740] ERROR: 1 destination item(s) changed type but could not be removed; left unchanged
[2026-10-27T13:52:52] Copying items to destination
[2026-10-27T13:52:52] Copying to /tmp/smp/ds/dst
[2026-10-27T13:52:52] Items: 4
[2026-10-27T13:52:52]   Directories: 1
[2026-10-27T13:52:52]   Files: 2
[2026-10-27T13:52:52]   Links: 0
[2026-10-27T13:52:52] Data: 4.000 B (2.000 B per file)
[2026-10-27T13:52:52] Skipped 1 special file(s) in the source (FIFO, socket or device): not copied
[2026-10-27T13:52:52] Creating 1 directories
[2026-10-27T13:52:52] Creating 2 files.
[2026-10-27T13:52:52] Copying data.
[2026-10-27T13:52:52] Copy data: 4.000 B (4 bytes)
[2026-10-27T13:52:52] Copy rate: 493.289 B/s (4 bytes in 0.008 seconds)
[2026-10-27T13:52:52] Syncing data to disk.
[2026-10-27T13:52:52] Sync completed in 0.020 seconds.
[2026-10-27T13:52:52] Setting ownership, permissions, and timestamps.
[2026-10-27T13:52:52] Updated 3 items in 0.000 seconds (7107.809 items/sec)
[2026-10-27T13:52:52] Syncing directory updates to disk.
[2026-10-27T13:52:52] Sync completed in 0.011 seconds.
[2026-10-27T13:52:52] Started: Oct-27-2026,13:52:52
[2026-10-27T13:52:52] Completed: Oct-27-2026,13:52:52
[2026-10-27T13:52:52] Seconds: 0.041
[2026-10-27T13:52:52] Items: 3
[2026-10-27T13:52:52]   Directories: 1
[2026-10-27T13:52:52]   Files: 2
[2026-10-27T13:52:52]   Links: 0
[2026-10-27T13:52:52] Data: 4.000 B (4 bytes)
[2026-10-27T13:52:52] Rate: 97.618 B/s (004 bytes in 0.041 seconds)
[2026-10-27T13:52:52] Updating timestamps on newly copied files
[2026-10-27T13:52:52] Completed updating timestamps
[2026-10-27T13:52:52] Completed sync
--------------------------------------------------------------------------
Primary job  terminated normally, but 1 process returned
a non-zero exit code. Per user-direction, the job has been aborted.
--------------------------------------------------------------------------
--------------------------------------------------------------------------
mpirun detected that one or more processes exited with non-zero status, thus causing
the job to be terminated. The first process to do so was:

  Process name: [[12188,1],0]
  Exit code:    1
--------------------------------------------------------------------------
"""

DSYNC_PREVIEW_RC = 0
DSYNC_PREVIEW = """\
[2026-10-27T13:52:51] Walking source path
[2026-10-27T13:52:51] Walking /tmp/smp/ds/src
[2026-10-27T13:52:51] Walked 6 items in 0.001 secs (6094.266 items/sec) ...
[2026-10-27T13:52:51] Walked 6 items in 0.001 seconds (5237.069 items/sec)
[2026-10-27T13:52:51] Walking destination path
[2026-10-27T13:52:51] Walking /tmp/smp/ds/dst
[2026-10-27T13:52:51] Walked 4 items in 0.000 secs (10387.371 items/sec) ...
[2026-10-27T13:52:51] Walked 4 items in 0.000 seconds (9607.325 items/sec)
[2026-10-27T13:52:51] Started   : Oct-27-2026, 13:52:51
[2026-10-27T13:52:51] Completed : Oct-27-2026, 13:52:51
[2026-10-27T13:52:51] Seconds   : 0.000
[2026-10-27T13:52:51] Items     : 0
[2026-10-27T13:52:51] Item Rate : 0 items in 0.000221 seconds (0.000000 items/sec)
[2026-10-27T13:52:51] Completed updating timestamps
[2026-10-27T13:52:51] Completed sync
"""

NSYNC_EXEC_ITEMS_FAILED_RC = 1
NSYNC_EXEC_ITEMS_FAILED = """\
[2026-10-27T13:52:40] Role assignment mode=map src_ranks=2 dst_ranks=1
[2026-10-27T13:52:40] Rank 0 role=src host=03875f669035
[2026-10-27T13:52:40] Rank 1 role=src host=03875f669035
[2026-10-27T13:52:40] Rank 2 role=dst host=03875f669035
[2026-10-27T13:52:40] Progress logging enabled on console log rank 0 (per batch)
[2026-10-27T13:52:40] Failed copy `ro/r1`: Permission denied (errno 13)
[2026-10-27T13:52:40] Failed copy `ro/r4`: Permission denied (errno 13)
[2026-10-27T13:52:40] Failed copy `ro/r5`: Permission denied (errno 13)
[2026-10-27T13:52:40] Failed copy `ro/r2`: Permission denied (errno 13)
[2026-10-27T13:52:40] Failed copy `ro/r3`: Permission denied (errno 13)
[2026-10-27T13:52:40] Progress 100.0% batch 1/1 actions=22 copied-files=20 copied-volume=100.000 B recent(actions=22 files=20 volume=100.000 B, 9086.60 files/s, 44.368 KiB/s over 0.002 s) avg(9086.60 files/s, 44.368 KiB/s)
[2026-10-27T13:52:40] [0] [/release/mpifileutils/src/nsync/nsync.c:897] ERROR: Failed items: 5 (copy 5); listed below: 5
[2026-10-27T13:52:40]   failed copy `ro/r1`: Permission denied (errno 13)
[2026-10-27T13:52:40]   failed copy `ro/r2`: Permission denied (errno 13)
[2026-10-27T13:52:40]   failed copy `ro/r3`: Permission denied (errno 13)
[2026-10-27T13:52:40]   failed copy `ro/r4`: Permission denied (errno 13)
[2026-10-27T13:52:40]   failed copy `ro/r5`: Permission denied (errno 13)
[2026-10-27T13:52:40] nsync Phase 5 planner+execute complete
[2026-10-27T13:52:40] Requested source: /tmp/smp/fail/src
[2026-10-27T13:52:40] Requested target: /tmp/smp/fail/dst
[2026-10-27T13:52:40] Options: dryrun=0 batch-files=0 delete=0 contents=0 role-mode=map imbalance-threshold=3.00 max-errors=65000 open-noatime=0
[2026-10-27T13:52:40] Role map: 0:src,1:src,2:dst
[2026-10-27T13:52:40] Metadata diff summary: only-src=27 only-dst=0 common=1 changed=1
[2026-10-27T13:52:40] Planned actions: copy=25 mkdir=2 symlink-update=0 meta-update=1 remove=0 skipped-dst-only=0
[2026-10-27T13:52:40] Completed actions: copy=20 mkdir=2 symlink-update=0 meta-update=1 remove=0 copied-bytes=100
[2026-10-27T13:52:40] [0] [/release/mpifileutils/src/nsync/nsync.c:9907] ERROR: Execution completed with errors: scan=0 exec=5
--------------------------------------------------------------------------
Primary job  terminated normally, but 1 process returned
a non-zero exit code. Per user-direction, the job has been aborted.
--------------------------------------------------------------------------
--------------------------------------------------------------------------
mpirun detected that one or more processes exited with non-zero status, thus causing
the job to be terminated. The first process to do so was:

  Process name: [[11830,1],0]
  Exit code:    1
--------------------------------------------------------------------------
"""

NSYNC_EXEC_MAX_ERRORS_RC = 1
NSYNC_EXEC_MAX_ERRORS = """\
[2026-10-27T13:52:47] Role assignment mode=map src_ranks=2 dst_ranks=1
[2026-10-27T13:52:47] Rank 0 role=src host=03875f669035
[2026-10-27T13:52:47] Rank 1 role=src host=03875f669035
[2026-10-27T13:52:47] Rank 2 role=dst host=03875f669035
[2026-10-27T13:52:47] Batch mode enabled: batch-files=1 batch-count=32 src-items=32 dst-items=1
[2026-10-27T13:52:47] Batch spool prepared: scan-records=33 scan-bytes=2956 batch-records=33 batch-sidecar-bytes=264
[2026-10-27T13:52:47] Progress logging enabled on console log rank 0 (per batch)
[2026-10-27T13:52:47] Failed copy `ro/r27`: Permission denied (errno 13)
[2026-10-27T13:52:47] Progress 3.1% batch 1/32 actions=0 copied-files=0 copied-volume=0.000 B recent(actions=0 files=0 volume=0.000 B, 0.00 files/s, 0.000 B/s over 0.001 s) avg(0.00 files/s, 0.000 B/s)
[2026-10-27T13:52:47] Failed copy `ro/r08`: Permission denied (errno 13)
[2026-10-27T13:52:47] Progress 6.2% batch 2/32 actions=0 copied-files=0 copied-volume=0.000 B recent(actions=0 files=0 volume=0.000 B, 0.00 files/s, 0.000 B/s over 0.001 s) avg(0.00 files/s, 0.000 B/s)
[2026-10-27T13:52:47] Failed copy `ro/r26`: Permission denied (errno 13)
[2026-10-27T13:52:47] Progress 9.4% batch 3/32 actions=0 copied-files=0 copied-volume=0.000 B recent(actions=0 files=0 volume=0.000 B, 0.00 files/s, 0.000 B/s over 0.000 s) avg(0.00 files/s, 0.000 B/s)
[2026-10-27T13:52:47] Stopping after batch 3/32: 3 failed item(s) (scan 0, other 3) reached --max-errors 3
[2026-10-27T13:52:47] [0] [/release/mpifileutils/src/nsync/nsync.c:897] ERROR: Failed items: 3 (copy 3); listed below: 3
[2026-10-27T13:52:47]   failed copy `ro/r08`: Permission denied (errno 13)
[2026-10-27T13:52:47]   failed copy `ro/r26`: Permission denied (errno 13)
[2026-10-27T13:52:47]   failed copy `ro/r27`: Permission denied (errno 13)
[2026-10-27T13:52:47] nsync Phase 5 planner+execute complete
[2026-10-27T13:52:47] Requested source: /tmp/smp/stop/src
[2026-10-27T13:52:47] Requested target: /tmp/smp/stop/dst
[2026-10-27T13:52:47] Options: dryrun=0 batch-files=1 delete=0 contents=0 role-mode=map imbalance-threshold=3.00 max-errors=3 open-noatime=0
[2026-10-27T13:52:47] Role map: 0:src,1:src,2:dst
[2026-10-27T13:52:47] Batch summary: batches=32 src-items=32 dst-items=1
[2026-10-27T13:52:47] Metadata diff summary: only-src=3 only-dst=0 common=1 changed=0
[2026-10-27T13:52:47] Planned actions: copy=3 mkdir=0 symlink-update=0 meta-update=0 remove=0 skipped-dst-only=0
[2026-10-27T13:52:47] Completed actions: copy=0 mkdir=0 symlink-update=0 meta-update=0 remove=0 copied-bytes=0
[2026-10-27T13:52:47] Planned and diff counts above cover batches 1-3 of 32 only (stopped early)
[2026-10-27T13:52:47] [0] [/release/mpifileutils/src/nsync/nsync.c:9907] ERROR: Execution completed with errors: scan=0 exec=3
--------------------------------------------------------------------------
Primary job  terminated normally, but 1 process returned
a non-zero exit code. Per user-direction, the job has been aborted.
--------------------------------------------------------------------------
--------------------------------------------------------------------------
mpirun detected that one or more processes exited with non-zero status, thus causing
the job to be terminated. The first process to do so was:

  Process name: [[11895,1],2]
  Exit code:    1
--------------------------------------------------------------------------
"""

NSYNC_EXEC_OK_RC = 0
NSYNC_EXEC_OK = """\
[2026-10-27T13:52:40] Role assignment mode=map src_ranks=2 dst_ranks=1
[2026-10-27T13:52:40] Rank 0 role=src host=03875f669035
[2026-10-27T13:52:40] Rank 1 role=src host=03875f669035
[2026-10-27T13:52:40] Rank 2 role=dst host=03875f669035
[2026-10-27T13:52:40] Progress logging enabled on console log rank 0 (per batch)
[2026-10-27T13:52:40] Progress 100.0% batch 1/1 actions=7 copied-files=3 copied-volume=9.000 B recent(actions=7 files=3 volume=9.000 B, 2080.78 files/s, 6.096 KiB/s over 0.001 s) avg(2080.78 files/s, 6.096 KiB/s)
[2026-10-27T13:52:40] nsync Phase 5 planner+execute complete
[2026-10-27T13:52:40] Requested source: /tmp/smp/ok/src
[2026-10-27T13:52:40] Requested target: /tmp/smp/ok/dst
[2026-10-27T13:52:40] Options: dryrun=0 batch-files=0 delete=1 contents=0 role-mode=map imbalance-threshold=3.00 max-errors=65000 open-noatime=0
[2026-10-27T13:52:40] Role map: 0:src,1:src,2:dst
[2026-10-27T13:52:40] Metadata diff summary: only-src=5 only-dst=2 common=1 changed=0
[2026-10-27T13:52:40] Planned actions: copy=3 mkdir=1 symlink-update=1 meta-update=0 remove=2 skipped-dst-only=0
[2026-10-27T13:52:40] Completed actions: copy=3 mkdir=1 symlink-update=1 meta-update=0 remove=2 copied-bytes=9
[2026-10-27T13:52:40] Execution completed successfully
"""

NSYNC_EXEC_QUIET_FAILED_RC = 1
NSYNC_EXEC_QUIET_FAILED = """\
--------------------------------------------------------------------------
Primary job  terminated normally, but 1 process returned
a non-zero exit code. Per user-direction, the job has been aborted.
--------------------------------------------------------------------------
--------------------------------------------------------------------------
mpirun detected that one or more processes exited with non-zero status, thus causing
the job to be terminated. The first process to do so was:

  Process name: [[11877,1],0]
  Exit code:    1
--------------------------------------------------------------------------
"""

NSYNC_EXEC_SPECIAL_SCANERR_RC = 1
NSYNC_EXEC_SPECIAL_SCANERR = """\
[2026-10-27T13:52:44] Role assignment mode=map src_ranks=2 dst_ranks=1
[2026-10-27T13:52:44] Rank 0 role=src host=03875f669035
[2026-10-27T13:52:44] Rank 1 role=src host=03875f669035
[2026-10-27T13:52:44] Rank 2 role=dst host=03875f669035
[2026-10-27T13:52:44] Progress logging enabled on console log rank 0 (per batch)
[2026-10-27T13:52:44] Failed scan `locked`: Permission denied (errno 13)
[2026-10-27T13:52:44] Source scan had 1 error(s): --delete is off for this run
[2026-10-27T13:52:44] Skipping `fifo`: a FIFO, socket or device in the source is not copied
[2026-10-27T13:52:44] Progress 100.0% batch 1/1 actions=2 copied-files=1 copied-volume=2.000 B recent(actions=2 files=1 volume=2.000 B, 892.37 files/s, 1.743 KiB/s over 0.001 s) avg(892.37 files/s, 1.743 KiB/s)
[2026-10-27T13:52:44] [0] [/release/mpifileutils/src/nsync/nsync.c:897] ERROR: Failed items: 1 (scan 1); listed below: 1
[2026-10-27T13:52:44]   failed scan `locked`: Permission denied (errno 13)
[2026-10-27T13:52:44] nsync Phase 5 planner+execute complete
[2026-10-27T13:52:44] Requested source: /tmp/smp/sp/src
[2026-10-27T13:52:44] Requested target: /tmp/smp/sp/dst
[2026-10-27T13:52:44] Options: dryrun=0 batch-files=0 delete=0 contents=0 role-mode=map imbalance-threshold=3.00 max-errors=65000 open-noatime=0
[2026-10-27T13:52:44] Role map: 0:src,1:src,2:dst
[2026-10-27T13:52:44] Metadata diff summary: only-src=2 only-dst=0 common=1 changed=1
[2026-10-27T13:52:44] Planned actions: copy=1 mkdir=1 symlink-update=0 meta-update=1 remove=0 skipped-dst-only=0
[2026-10-27T13:52:44] Completed actions: copy=1 mkdir=1 symlink-update=0 meta-update=1 remove=0 copied-bytes=2
[2026-10-27T13:52:44] Skipped 1 special file(s) in the source (FIFO, socket or device): not copied, the destination entry at that name is left as it is
[2026-10-27T13:52:44] [0] [/release/mpifileutils/src/nsync/nsync.c:9898] ERROR: Encountered 1 scan error(s)
[2026-10-27T13:52:44] [0] [/release/mpifileutils/src/nsync/nsync.c:9907] ERROR: Execution completed with errors: scan=1 exec=0
--------------------------------------------------------------------------
Primary job  terminated normally, but 1 process returned
a non-zero exit code. Per user-direction, the job has been aborted.
--------------------------------------------------------------------------
--------------------------------------------------------------------------
mpirun detected that one or more processes exited with non-zero status, thus causing
the job to be terminated. The first process to do so was:

  Process name: [[11845,1],2]
  Exit code:    1
--------------------------------------------------------------------------
"""

NSYNC_EXEC_TYPE_CHANGE_RC = 1
NSYNC_EXEC_TYPE_CHANGE = """\
[2026-10-27T13:52:43] Role assignment mode=map src_ranks=2 dst_ranks=1
[2026-10-27T13:52:43] Rank 0 role=src host=03875f669035
[2026-10-27T13:52:43] Rank 1 role=src host=03875f669035
[2026-10-27T13:52:43] Rank 2 role=dst host=03875f669035
[2026-10-27T13:52:43] Progress logging enabled on console log rank 0 (per batch)
[2026-10-27T13:52:43] Failed replace-dir `d`: Directory not empty (errno 39)
[2026-10-27T13:52:43] Progress 100.0% batch 1/1 actions=1 copied-files=1 copied-volume=5.000 B recent(actions=1 files=1 volume=5.000 B, 688.99 files/s, 3.364 KiB/s over 0.001 s) avg(688.99 files/s, 3.364 KiB/s)
[2026-10-27T13:52:43] [0] [/release/mpifileutils/src/nsync/nsync.c:897] ERROR: Failed items: 1 (replace-dir 1); listed below: 1
[2026-10-27T13:52:43]   failed replace-dir `d`: Directory not empty (errno 39)
[2026-10-27T13:52:43] nsync Phase 5 planner+execute complete
[2026-10-27T13:52:43] Requested source: /tmp/smp/tc/src
[2026-10-27T13:52:43] Requested target: /tmp/smp/tc/dst
[2026-10-27T13:52:43] Options: dryrun=0 batch-files=0 delete=0 contents=0 role-mode=map imbalance-threshold=3.00 max-errors=65000 open-noatime=0
[2026-10-27T13:52:43] Role map: 0:src,1:src,2:dst
[2026-10-27T13:52:43] Metadata diff summary: only-src=1 only-dst=1 common=2 changed=1
[2026-10-27T13:52:43] Planned actions: copy=2 mkdir=0 symlink-update=0 meta-update=0 remove=1 skipped-dst-only=1
[2026-10-27T13:52:43] Completed actions: copy=1 mkdir=0 symlink-update=0 meta-update=0 remove=0 copied-bytes=5
[2026-10-27T13:52:43] Type changes: to-directory 0, from-directory 1
[2026-10-27T13:52:43] A directory that is replaced by a non-directory must be empty unless --delete is given; non-empty ones are left alone and reported as failed
[2026-10-27T13:52:43] [0] [/release/mpifileutils/src/nsync/nsync.c:9907] ERROR: Execution completed with errors: scan=0 exec=1
--------------------------------------------------------------------------
Primary job  terminated normally, but 1 process returned
a non-zero exit code. Per user-direction, the job has been aborted.
--------------------------------------------------------------------------
--------------------------------------------------------------------------
mpirun detected that one or more processes exited with non-zero status, thus causing
the job to be terminated. The first process to do so was:

  Process name: [[11819,1],2]
  Exit code:    1
--------------------------------------------------------------------------
"""

NSYNC_PREVIEW_OK_RC = 0
NSYNC_PREVIEW_OK = """\
[2026-10-27T13:52:40] Role assignment mode=map src_ranks=2 dst_ranks=1
[2026-10-27T13:52:40] Rank 0 role=src host=03875f669035
[2026-10-27T13:52:40] Rank 1 role=src host=03875f669035
[2026-10-27T13:52:40] Rank 2 role=dst host=03875f669035
[2026-10-27T13:52:40] Progress logging enabled on console log rank 0 (per batch)
[2026-10-27T13:52:40] Progress 100.0% batch 1/1 planned-actions=7 planned-copy-files=3 planned-volume=9.000 B
[2026-10-27T13:52:40] nsync Phase 5 planner dryrun complete
[2026-10-27T13:52:40] Requested source: /tmp/smp/ok/src
[2026-10-27T13:52:40] Requested target: /tmp/smp/ok/dst
[2026-10-27T13:52:40] Options: dryrun=1 batch-files=0 delete=1 contents=0 role-mode=map imbalance-threshold=3.00 max-errors=65000 open-noatime=0
[2026-10-27T13:52:40] Role map: 0:src,1:src,2:dst
[2026-10-27T13:52:40] Metadata diff summary: only-src=5 only-dst=2 common=1 changed=0
[2026-10-27T13:52:40] Planned actions: copy=3 mkdir=1 symlink-update=1 meta-update=0 remove=2 skipped-dst-only=0
[2026-10-27T13:52:40] Dryrun enabled: planner generated actions but no filesystem changes were made.
"""

NSYNC_PREVIEW_TYPE_CHANGE_RC = 0
NSYNC_PREVIEW_TYPE_CHANGE = """\
[2026-10-27T13:52:43] Role assignment mode=map src_ranks=2 dst_ranks=1
[2026-10-27T13:52:43] Rank 0 role=src host=03875f669035
[2026-10-27T13:52:43] Rank 1 role=src host=03875f669035
[2026-10-27T13:52:43] Rank 2 role=dst host=03875f669035
[2026-10-27T13:52:43] Progress logging enabled on console log rank 0 (per batch)
[2026-10-27T13:52:43] Progress 100.0% batch 1/1 planned-actions=3 planned-copy-files=2 planned-volume=16.000 B
[2026-10-27T13:52:43] nsync Phase 5 planner dryrun complete
[2026-10-27T13:52:43] Requested source: /tmp/smp/tc/src
[2026-10-27T13:52:43] Requested target: /tmp/smp/tc/dst
[2026-10-27T13:52:43] Options: dryrun=1 batch-files=0 delete=0 contents=0 role-mode=map imbalance-threshold=3.00 max-errors=65000 open-noatime=0
[2026-10-27T13:52:43] Role map: 0:src,1:src,2:dst
[2026-10-27T13:52:43] Metadata diff summary: only-src=1 only-dst=1 common=2 changed=1
[2026-10-27T13:52:43] Planned actions: copy=2 mkdir=0 symlink-update=0 meta-update=0 remove=1 skipped-dst-only=1
[2026-10-27T13:52:43] Type changes: to-directory 0, from-directory 1
[2026-10-27T13:52:43] A directory that is replaced by a non-directory must be empty unless --delete is given; non-empty ones are left alone and reported as failed
[2026-10-27T13:52:43] Dryrun enabled: planner generated actions but no filesystem changes were made.
"""

_BANNER = "--------------------------------------------------------------------------\n"


def _split(sample):
    """2>&1 표본 -> (stdout, stderr): MFU 줄은 stdout(MFU_LOG), 끝의 mpirun 배너는 stderr(실제 러너가 받는 모양)."""
    i = sample.find(_BANNER)
    return (sample, "") if i < 0 else (sample[:i], sample[i:])


def _summary(tool, sample, rc, *, dryrun=False, quiet=False):
    out, err = _split(sample)
    return _build_summary(tool, out, rc, "/nonexistent-artifacts", dryrun=dryrun, quiet=quiet, stderr=err)


def _head(tool, dryrun):
    return {"detail_version": 2, "tool": tool, "dryrun": dryrun}


# ---- 계약: 사유 집합은 제어면 화이트리스트와 같다 ----

def test_runner_tool_failure_reasons_match_the_control_plane():
    # 러너는 dms 를 import 하지 않는다 -- 두 집합이 갈라지면 스테퍼가 마커를 승격하지 못하고 execution_failed 로 뭉갠다.
    from dms.execution_manifests import EXECUTION_REASONS, TOOL_FAILURE_REASONS
    assert runner.TOOL_FAILURE_REASONS == TOOL_FAILURE_REASONS
    assert runner.TOOL_FAILURE_REASONS <= EXECUTION_REASONS
    assert {runner.TOOL_ABORTED, runner.DESTINATION_FULL, runner.DESTINATION_READ_ONLY, runner.SYNC_STOPPED_MAX_ERRORS,
            runner.SYNC_TYPE_CHANGE_REFUSED, runner.SOURCE_SCAN_INCOMPLETE, runner.SYNC_ITEMS_FAILED,
            runner.RM_ITEMS_FAILED, runner.PREVIEW_SCAN_INCOMPLETE} == runner.TOOL_FAILURE_REASONS


# ---- nsync ----

def test_nsync_execution_ok():
    assert _summary("nsync", NSYNC_EXEC_OK, NSYNC_EXEC_OK_RC) == {
        "returncode": 0, "files": 7, "bytes": 9, **_head("nsync", False),
        "planned": {"copy": 3, "mkdir": 1, "symlink_update": 1, "meta_update": 0, "remove": 2, "skipped_dst_only": 0},
        "done": {"copy": 3, "mkdir": 1, "symlink_update": 1, "meta_update": 0, "remove": 2},
        "copied_bytes": 9}


def test_nsync_preview_ok_counts_planned_changes():
    assert _summary("nsync", NSYNC_PREVIEW_OK, NSYNC_PREVIEW_OK_RC, dryrun=True) == {
        "returncode": 0, "files": 7, "bytes": 9, **_head("nsync", True),
        "planned": {"copy": 3, "mkdir": 1, "symlink_update": 1, "meta_update": 0, "remove": 2, "skipped_dst_only": 0},
        "planned_bytes": 9}


def test_nsync_items_failed_files_are_what_took_effect_not_the_plan():
    # 포탈 괴리 [2]·[4]: 예전 files 는 Planned 합(25+2+1 = 28)이라 실패한 잡도 「복사한 파일 28」이었다. 실제로 반영된 것은
    # Completed 합(20+2+1 = 23)이다.
    s = _summary("nsync", NSYNC_EXEC_ITEMS_FAILED, NSYNC_EXEC_ITEMS_FAILED_RC)
    assert (s["files"], s["bytes"]) == (23, 100)
    assert s["done"] == {"copy": 20, "mkdir": 2, "symlink_update": 0, "meta_update": 1, "remove": 0}
    assert s["planned"]["copy"] == 25
    assert s["failures"] == {"total": 5, "by_op": {"copy": 5}}
    # 목록 줄(경로순) 그대로 -- 즉시 줄(Failed copy, 랭크 도착 순)은 샘플이 아니다.
    assert s["failure_samples"] == [{"op": "copy", "path": f"ro/r{i}", "errno": 13} for i in range(1, 6)]
    assert s["reason"] == "sync_items_failed"
    assert "stop_cause" not in s and "stopped_early" not in s


def test_nsync_type_change_refused_execution():
    s = _summary("nsync", NSYNC_EXEC_TYPE_CHANGE, NSYNC_EXEC_TYPE_CHANGE_RC)
    assert (s["files"], s["bytes"]) == (1, 5)
    assert s["type_changes"] == {"to_dir": 0, "from_dir": 1}
    assert s["type_change_needs_delete"] is True
    assert s["type_change_refused"] == 1
    assert s["failures"] == {"total": 1, "by_op": {"replace-dir": 1}}
    assert s["failure_samples"] == [{"op": "replace-dir", "path": "d", "errno": 39}]
    assert s["reason"] == "sync_type_change_refused"


def test_nsync_replace_dir_failure_with_delete_on_is_not_a_delete_off_refusal():
    # --delete 인데 재귀 제거가 ENOTEMPTY 로 끝난 경우(동시에 생긴 항목)도 replace-dir 로 기록된다 -- nsync 는 「--delete 를 주지
    # 않으면」 줄을 찍지 않는다(!opts.delete 일 때만). 「삭제를 켜고 다시 실행하세요」는 거짓이다(d192 D1).
    out = (NSYNC_EXEC_TYPE_CHANGE.split(_BANNER)[0]
           .replace("delete=0", "delete=1")
           .replace("[2026-10-27T13:52:43] A directory that is replaced by a non-directory must be empty unless --delete is "
                    "given; non-empty ones are left alone and reported as failed\n", ""))
    s = _build_summary("nsync", out, 1, "/x")
    assert s["type_change_refused"] == 1 and "type_change_needs_delete" not in s
    assert s["reason"] == "sync_items_failed"


def test_nsync_preview_carries_the_type_change_warning():
    # 포탈 괴리 [6]: 미리보기 rc 0 인데 도구는 이미 「삭제를 켜지 않으면 실패로 남는다」고 경고했다 -- 컨펌 창이 보여줄 수 있게.
    assert _summary("nsync", NSYNC_PREVIEW_TYPE_CHANGE, NSYNC_PREVIEW_TYPE_CHANGE_RC, dryrun=True) == {
        "returncode": 0, "files": 3, "bytes": 16, **_head("nsync", True),
        "planned": {"copy": 2, "mkdir": 0, "symlink_update": 0, "meta_update": 0, "remove": 1, "skipped_dst_only": 1},
        "planned_bytes": 16, "type_changes": {"to_dir": 0, "from_dir": 1}, "type_change_needs_delete": True}


def test_nsync_special_files_and_source_scan_error():
    s = _summary("nsync", NSYNC_EXEC_SPECIAL_SCANERR, NSYNC_EXEC_SPECIAL_SCANERR_RC)
    assert s["skipped_special"] == 1
    # 「Source scan had 1 error(s): --delete is off」 -- 소스 쪽 오류 수가 따로 실린다(d192 D2). 전부 소스 쪽이고 delete 가
    # 실제로 꺼졌으니 「소스 일부를 읽지 못해 삭제하지 않았다」가 참이다.
    assert s["scan_errors"] == 1 and s["source_scan_errors"] == 1 and s["delete_disabled"] is True
    assert s["failures"] == {"total": 1, "by_op": {"scan": 1}}
    assert s["failure_samples"] == [{"op": "scan", "path": "locked", "errno": 13}]
    assert s["reason"] == "source_scan_incomplete"
    assert s["files"] == 3


# 합성(d192 리뷰 runner-2 재현 -- 실 바이너리 출력의 모양): 비 root nsync --delete, 목적지에 읽을 수 없는 0700 디렉토리 + 목적지
# 전용 파일 3개. 스캔 오류는 목적지 역할이라 --delete 는 꺼지지 않았고(「Source scan had」 줄 없음) 삭제 3개가 돌았다.
NSYNC_EXEC_DST_SCANERR = """\
[2026-10-27T14:10:00] Failed scan `locked`: Permission denied (errno 13)
[2026-10-27T14:10:00] [0] [/release/mpifileutils/src/nsync/nsync.c:897] ERROR: Failed items: 1 (scan 1); listed below: 1
[2026-10-27T14:10:00]   failed scan `locked`: Permission denied (errno 13)
[2026-10-27T14:10:00] nsync Phase 5 planner+execute complete
[2026-10-27T14:10:00] Options: dryrun=0 batch-files=0 delete=1 contents=0 role-mode=map imbalance-threshold=3.00 max-errors=65000 open-noatime=0
[2026-10-27T14:10:00] Planned actions: copy=0 mkdir=0 symlink-update=0 meta-update=0 remove=3 skipped-dst-only=0
[2026-10-27T14:10:00] Completed actions: copy=0 mkdir=0 symlink-update=0 meta-update=0 remove=3 copied-bytes=0
[2026-10-27T14:10:00] [0] [/release/mpifileutils/src/nsync/nsync.c:9898] ERROR: Encountered 1 scan error(s)
[2026-10-27T14:10:00] [0] [/release/mpifileutils/src/nsync/nsync.c:9907] ERROR: Execution completed with errors: scan=1 exec=0
"""


def test_nsync_destination_scan_error_is_not_a_source_scan_reason():
    # 예전: reason=source_scan_incomplete -- 「소스 일부를 읽지 못해 … 삭제를 하지 않았습니다」인데 목적지를 못 읽었고 삭제 3개가
    # 돌았다(둘 다 거짓). nsync 가 delete 를 끈 줄이 없으면 쪽을 확인할 수 없다 -- 일반 항목 실패다.
    s = _build_summary("nsync", NSYNC_EXEC_DST_SCANERR, 1, "/x")
    assert s["scan_errors"] == 1 and s["done"]["remove"] == 3
    assert "source_scan_errors" not in s and "delete_disabled" not in s
    assert s["reason"] == "sync_items_failed"
    # 같은 트리의 미리보기는 미리보기 사유 그대로다 -- 문구가 쪽을 말하지 않는다(포탈 몫).
    preview = NSYNC_EXEC_DST_SCANERR.replace("dryrun=0", "dryrun=1").replace(
        "[2026-10-27T14:10:00] Completed actions: copy=0 mkdir=0 symlink-update=0 meta-update=0 remove=3 copied-bytes=0\n", "")
    assert _build_summary("nsync", preview, 1, "/x", dryrun=True)["reason"] == "preview_scan_incomplete"


def test_nsync_source_scan_count_must_cover_every_scan_error():
    # 소스 1 + 목적지 1: delete 는 꺼졌지만 「스캔 오류가 전부 소스 쪽」이 아니다 -- 일반 항목 실패.
    out = (NSYNC_EXEC_SPECIAL_SCANERR.split(_BANNER)[0]
           .replace("ERROR: Encountered 1 scan error(s)", "ERROR: Encountered 2 scan error(s)")
           .replace("Failed items: 1 (scan 1); listed below: 1", "Failed items: 2 (scan 2); listed below: 2"))
    s = _build_summary("nsync", out, 1, "/x")
    assert (s["scan_errors"], s["source_scan_errors"]) == (2, 1)
    assert s["reason"] == "sync_items_failed"


def test_nsync_max_errors_stop():
    s = _summary("nsync", NSYNC_EXEC_MAX_ERRORS, NSYNC_EXEC_MAX_ERRORS_RC)
    # 0 은 정상값이다(아무것도 반영되지 않았다) -- 모름(null)과 다르다.
    assert (s["files"], s["bytes"]) == (0, 0)
    assert s["stopped_early"] is True and s["stop_cause"] == "max_errors"
    assert s["reason"] == "sync_stopped_max_errors"


def test_nsync_quiet_failure_has_no_details_and_no_reason():
    # -q 면 MFU_LOG 가 아무것도 찍지 않는다(ERROR 줄까지) -- 상세는 모름이지 0 이 아니다.
    s = _summary("nsync", NSYNC_EXEC_QUIET_FAILED, NSYNC_EXEC_QUIET_FAILED_RC, quiet=True)
    assert s == {"returncode": 1, "files": None, "bytes": None, **_head("nsync", False), "quiet": True}


def test_nsync_execution_without_completed_line_is_unknown_not_the_plan():
    # Completed 줄이 없는 출력(옛 빌드 등)에서 계획 수를 「반영한 수」로 지어내지 않는다 -- 바이트만 진행 줄로 물러난다.
    out = "\n".join(line for line in NSYNC_EXEC_OK.splitlines() if "Completed actions" not in line) + "\n"
    s = _build_summary("nsync", out, 0, "/x")
    assert (s["files"], s["bytes"]) == (None, 9)
    assert "done" not in s and "copied_bytes" not in s


def test_nsync_stop_cause_from_errno_when_destination_is_full():
    # 합성(ENOSPC 표본 없음): 배치 1개 실행 안에서 멈추면 「Stopping after batch」 줄이 없다 -- 실패 errno 로 가른다.
    out = ("[2026-10-27T13:00:00] Failed copy `big`: No space left on device (errno 28)\n"
           "[2026-10-27T13:00:00] [0] [nsync.c:897] ERROR: Failed items: 1 (copy 1); listed below: 1\n"
           "[2026-10-27T13:00:00]   failed copy `big`: No space left on device (errno 28)\n"
           "[2026-10-27T13:00:00] Planned actions: copy=9 mkdir=0 symlink-update=0 meta-update=0 remove=0 skipped-dst-only=0\n"
           "[2026-10-27T13:00:00] Completed actions: copy=3 mkdir=0 symlink-update=0 meta-update=0 remove=0 copied-bytes=30\n"
           "[2026-10-27T13:00:00] Not attempted after a stop: 5 action(s) of the batch in progress (--max-errors reached "
           "or the destination is full or read-only)\n"
           "[2026-10-27T13:00:00] [0] [nsync.c:9907] ERROR: Execution completed with errors: scan=0 exec=1\n")
    s = _build_summary("nsync", out, 1, "/x")
    assert s["not_attempted"] == 5 and s["stop_cause"] == "destination_full"
    assert s["reason"] == "destination_full"
    ro = out.replace("No space left on device (errno 28)", "Read-only file system (errno 30)")
    assert _build_summary("nsync", ro, 1, "/x")["stop_cause"] == "destination_read_only"
    # 가득 참·읽기 전용 errno 가 없으면 남는 원인은 max-errors 몫이다.
    perm = out.replace("No space left on device (errno 28)", "Permission denied (errno 13)")
    s = _build_summary("nsync", perm, 1, "/x")
    assert s["stop_cause"] == "max_errors" and s["reason"] == "sync_stopped_max_errors"


def test_nsync_other_summary_lines():
    # 합성(nsync.c 9849·9880·9889 의 형식): 메타데이터·noatime 줄.
    out = ("[2026-10-27T13:00:00] --open-noatime: 4 source file(s) not owned by this run were read without O_NOATIME\n"
           "[2026-10-27T13:00:00] Metadata best-effort applied with 2 ignored operation(s): chown=2 chmod=0 utime=0\n"
           "[2026-10-27T13:00:00] [0] [nsync.c:9889] ERROR: Metadata apply had 1 hard failure(s): chown=0 chmod=1 utime=0\n")
    d = parse_tool_detail("nsync", out, "", False)
    assert (d["noatime_fallbacks"], d["metadata_ignored"], d["metadata_failed"]) == (4, 2, 1)


# ---- dsync ----

def test_dsync_preview_reports_the_whole_source_not_a_change_count():
    assert _summary("dsync", DSYNC_PREVIEW, DSYNC_PREVIEW_RC, dryrun=True) == {
        "returncode": 0, "files": 6, "bytes": None, **_head("dsync", True), "source_items": 6, "tool_errors": 0}


def test_dsync_items_failed_subtracts_failed_copies():
    # 최종 Items: 2 인데 그중 1개는 열지 못해 만들어지지 않았다 -- 예전 files 는 2 였다.
    s = _summary("dsync", DSYNC_EXEC_ITEMS_FAILED, DSYNC_EXEC_ITEMS_FAILED_RC)
    assert (s["files"], s["bytes"]) == (1, 3)
    assert (s["copied_items"], s["copy_failed"], s["copied_bytes"]) == (1, 1, 3)
    assert s["failure_samples"] == [{"op": "open", "path": "/tmp/smp/dsf/src/no", "errno": 13}]
    assert s["tool_errors"] == 2
    assert s["reason"] == "sync_items_failed"


def test_dsync_type_change_refused_execution():
    s = _summary("dsync", DSYNC_EXEC_TYPE_CHANGE, DSYNC_EXEC_TYPE_CHANGE_RC)
    assert (s["files"], s["bytes"]) == (3, 4)
    assert (s["removed"], s["remove_failed"], s["type_change_refused"]) == (0, 1, 1)
    assert s["skipped_special"] == 1 and s["copy_failed"] == 0 and s["create_failed"] == 0
    assert s["failure_samples"] == [{"op": "rmdir", "path": "/tmp/smp/ds/dst/d", "errno": 39}]
    # 막힌 타입 교체가 제거 실패로도 세진다 -- 남은 것이 디렉토리 + delete 꺼짐(줄 꼬리)이고 rmdir errno 39 뿐이니 「삭제를 켜면
    # 바뀐다」가 참이다.
    assert s["reason"] == "sync_type_change_refused"
    # 사유 판정 근거(밑줄 키)는 summary 계약 밖이다.
    assert not [k for k in s if k.startswith("_")]
    d = parse_tool_detail("dsync", _split(DSYNC_EXEC_TYPE_CHANGE)[0], "", False)
    assert (d["_type_change_dir_refused"], d["_remove_errnos"]) == (1, [39])


# 합성(d192 리뷰 runner-1·contract-33 재현의 모양 -- dsync.c 1724·1740, mfu_flist_remove.c 76·84·761 형식): 실 표본
# DSYNC_EXEC_TYPE_CHANGE 의 제거·타입 교체 줄만 바꾼다.
_DS_TC_RMDIR = ("[2026-10-27T13:52:52] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:76] ERROR: Failed to rmdir "
                "`/tmp/smp/ds/dst/d' (errno=39 Directory not empty)\n")
_DS_TC_LINE = ("[2026-10-27T13:52:52] [1] [/release/mpifileutils/src/dsync/dsync.c:1724] ERROR: Type change not applied: "
               "`/tmp/smp/ds/dst/d' could not be removed (still a directory), a non-empty directory is replaced only with "
               "--delete; source item not copied or linked\n")


def _dsync_type_change(rmdir_line, type_line):
    out = _split(DSYNC_EXEC_TYPE_CHANGE)[0]
    assert _DS_TC_RMDIR in out and _DS_TC_LINE in out
    return out.replace(_DS_TC_RMDIR, rmdir_line).replace(_DS_TC_LINE, type_line)


def test_dsync_type_change_refusal_from_permission_is_not_the_delete_off_reason():
    # (a) 목적지의 파일 f 를 지울 권한이 없다(EPERM -- 소스 f 는 디렉토리): 남은 것은 비 디렉토리, 꼬리 없음.
    out = _dsync_type_change(
        "[2026-10-27T13:52:52] [0] [/release/mpifileutils/src/common/mfu_flist_remove.c:84] ERROR: Failed to unlink "
        "`/tmp/smp/ds/dst/f' (errno=1 Operation not permitted)\n",
        "[2026-10-27T13:52:52] [1] [/release/mpifileutils/src/dsync/dsync.c:1724] ERROR: Type change not applied: "
        "`/tmp/smp/ds/dst/f' could not be removed (still a non-directory); source item not copied or linked\n")
    s = _build_summary("dsync", out, 1, "/x")
    assert (s["type_change_refused"], s["remove_failed"], s["copy_failed"]) == (1, 1, 0)
    assert s["reason"] == "sync_items_failed"            # 예전: sync_type_change_refused(「삭제를 켜면 바뀝니다」)
    # (c) delete 꺼짐 + 디렉토리 생존자(꼬리 있음)인데 rmdir 이 EACCES -- 빈 디렉토리여도 꼬리는 붙는다(dsync.c 1724). 삭제를
    # 켜도 안 바뀐다.
    out = _dsync_type_change(_DS_TC_RMDIR.replace("errno=39 Directory not empty", "errno=13 Permission denied"),
                             _DS_TC_LINE)
    assert _build_summary("dsync", out, 1, "/x")["reason"] == "sync_items_failed"


def test_dsync_type_change_refusal_with_delete_on_is_not_the_delete_off_reason():
    # (b) --delete 인데 디렉토리를 지우지 못했다(EACCES) -- 꼬리 「…replaced only with --delete」는 delete 가 꺼졌을 때만 붙는다.
    out = _dsync_type_change(
        _DS_TC_RMDIR.replace("errno=39 Directory not empty", "errno=13 Permission denied"),
        "[2026-10-27T13:52:52] [1] [/release/mpifileutils/src/dsync/dsync.c:1724] ERROR: Type change not applied: "
        "`/tmp/smp/ds/dst/d' could not be removed (still a directory); source item not copied or linked\n")
    s = _build_summary("dsync", out, 1, "/x")
    assert s["type_change_refused"] == 1 and s["reason"] == "sync_items_failed"
    # 같은 꼬리 없는 줄 + errno 39 여도(delete 켜짐에 동시 생성 등) 「삭제를 켜라」가 아니다.
    out = _dsync_type_change(
        _DS_TC_RMDIR,
        "[2026-10-27T13:52:52] [1] [/release/mpifileutils/src/dsync/dsync.c:1724] ERROR: Type change not applied: "
        "`/tmp/smp/ds/dst/d' could not be removed (still a directory); source item not copied or linked\n")
    assert _build_summary("dsync", out, 1, "/x")["reason"] == "sync_items_failed"


def test_dsync_type_change_suffix_cannot_be_forged_by_a_name():
    # 비 디렉토리 생존자의 이름에 꼬리 문구를 넣어도 줄 끝(「(still a non-directory); source item …」)이 달라 맞지 않는다.
    name = ("/tmp/smp/ds/dst/x' could not be removed (still a directory), a non-empty directory is replaced only with "
            "--delete; source item not copied or linked")
    out = _dsync_type_change(
        f"[2026-10-27T13:52:52] [0] [mfu_flist_remove.c:84] ERROR: Failed to unlink `{name}' (errno=1 Operation not "
        "permitted)\n",
        f"[2026-10-27T13:52:52] [1] [dsync.c:1724] ERROR: Type change not applied: `{name}' could not be removed (still a "
        "non-directory); source item not copied or linked\n")
    d = parse_tool_detail("dsync", out, "", False)
    assert d["_type_change_dir_refused"] == 0 and d["_remove_errnos"] == [1]
    assert _derive_reason("dsync", d) == "sync_items_failed"
    # 이름에 제어 문자가 있으면 dsync 는 \xHH 로 찍는다 -- 그런 이름은 날 경로인 제 rmdir 줄을 쪼개 errno 를 숨길 수 있어 세지
    # 않는다(수가 어긋나 일반 항목 실패).
    escaped = _DS_TC_LINE.replace("`/tmp/smp/ds/dst/d'", "`/tmp/smp/ds/dst/d\\x0aevil'")
    hidden = _dsync_type_change(
        "[2026-10-27T13:52:52] [0] [mfu_flist_remove.c:76] ERROR: Failed to rmdir `/tmp/smp/ds/dst/d\n"
        "[2026-10-27T13:52:52] [0] [mfu_flist_remove.c:76] ERROR: Failed to rmdir `/y' (errno=39 Directory not empty)\n"
        "evil' (errno=13 Permission denied)\n", escaped)
    d = parse_tool_detail("dsync", hidden, "", False)
    assert d["_remove_errnos"] == [39]                    # 진짜 errno 13 은 쪼개진 조각이라 안 보인다
    assert d["_type_change_dir_refused"] == 0
    assert _derive_reason("dsync", d) == "sync_items_failed"


# ---- dsync 최종 블록·중단·만들기 실패(d192 D6·D7) ----

def _cut_after(sample, line):
    out = _split(sample)[0]
    i = out.index(line)
    return out[:i + len(line)]


def test_dsync_aborted_mid_copy_does_not_report_the_copy_plan_as_copied():
    # 실 표본을 「Copying data.」 뒤에서 자르고 stderr 에 MPI 중단(direct+bufsize 의 mfu_abort)을 둔다. 남은 "Items: 2" 는 복사
    # **계획**(FIFO 까지 센다)이다 -- 예전엔 files=2·copied_items=2 로 「반영한 항목 2」·통계 합계에 들어갔다.
    out = _cut_after(DSYNC_EXEC_DELETE_OK, "[2026-10-27T13:52:53] Copying data.\n")
    err = ("ABORT: rank 0 on HOST: O_DIRECT requires chunk size to be integer multiple of block size 3145728 "
           "@ mfu_flist_copy.c:1730\n"
           "MPI_ABORT was invoked on rank 0 in communicator MPI_COMM_WORLD\nwith errorcode -1.\n")
    s = _build_summary("dsync", out, 134, "/x", stderr=err)
    assert (s["files"], s["bytes"]) == (None, None)
    assert "copied_items" not in s and "copied_bytes" not in s
    assert s["removed"] == 3 and s["aborted"] is True and s["reason"] == "tool_aborted"
    # 중단 흔적이 없어도(강제 종료 rc 137) 최종 블록이 없으면 모름이다 -- 계획 줄로 지어내지 않는다.
    s = _build_summary("dsync", out, 137, "/x")
    assert (s["files"], s["bytes"]) == (None, None) and "copied_items" not in s
    # 포탈 괴리 runner/portal-jobs-12 의 모양(계획 1개, direct 표본)도 같다.
    cut = _cut_after(DSYNC_EXEC_DIRECT_BAD_BUFSIZE, "[2026-10-27T13:52:58] Copying data.\n")
    assert _build_summary("dsync", cut, 134, "/x", stderr=err)["files"] is None


def test_dsync_aborted_after_the_final_block_is_still_unknown():
    # d192 D6: 중단으로 끝난 실행의 수는 최종 블록이 있어도 「반영」으로 보이지 않는다.
    out, _ = _split(DSYNC_EXEC_DELETE_OK)
    s = _build_summary("dsync", out, 134, "/x", stderr="MPI_ABORT was invoked on rank 0\n")
    assert (s["files"], s["bytes"]) == (None, None) and "copied_items" not in s and "copied_bytes" not in s


# 합성(d192 리뷰 runner-6 재현의 모양 -- mfu_flist_copy.c 1273·2101·2256 형식): 비 root, 목적지 a 가 root 소유 755, 소스
# a/1..3 + b/1..7. a 아래 세 파일은 mknod 부터 실패해 최종 Items(8 = 디렉토리 b + 파일 7)에 없는데, 복사 단계에서 「Failed to
# copy」가 한 번 더 찍힌다.
_TS6 = "[2026-10-27T14:20:00] "
DSYNC_EXEC_MKNOD_FAILED = (
    f"{_TS6}Copying to /t/dst\n{_TS6}Items: 12\n{_TS6}  Directories: 2\n{_TS6}  Files: 10\n{_TS6}  Links: 0\n"
    f"{_TS6}Data: 10.000 B (1.000 B per file)\n"
    f"{_TS6}Original directory exists, skip the creation: `/t/dst/a' (errno=17 File exists)\n"
    f"{_TS6}Creating 10 files.\n"
    + "".join(f"{_TS6}[0] [/release/mpifileutils/src/common/mfu_flist_copy.c:1273] ERROR: File `/t/dst/a/{i}' mknod() "
              "failed (errno=13 Permission denied)\n" for i in (1, 2, 3))
    + f"{_TS6}Copying data.\n"
    + "".join(f"{_TS6}[0] [/release/mpifileutils/src/common/mfu_flist_copy.c:2101] ERROR: Failed to open output file "
              f"`/t/dst/a/{i}' (errno=13 Permission denied)\n" for i in (1, 2, 3))
    + "".join(f"{_TS6}[0] [/release/mpifileutils/src/common/mfu_flist_copy.c:2256] ERROR: Failed to copy `/t/src/a/{i}' "
              f"to `/t/dst/a/{i}'\n" for i in (1, 2, 3))
    + f"{_TS6}Copy data: 10.000 B (10 bytes)\n"
    f"{_TS6}Started: Oct-27-2026,14:20:00\n{_TS6}Completed: Oct-27-2026,14:20:00\n{_TS6}Seconds: 0.050\n"
    f"{_TS6}Items: 8\n{_TS6}  Directories: 1\n{_TS6}  Files: 7\n{_TS6}  Links: 0\n{_TS6}Data: 7.000 B (7 bytes)\n"
    f"{_TS6}Rate: 140.000 B/s (007 bytes in 0.050 seconds)\n{_TS6}Completed sync\n")


def test_dsync_mknod_failures_are_not_subtracted_twice():
    s = _build_summary("dsync", DSYNC_EXEC_MKNOD_FAILED, 1, "/x")
    # 예전: 8 − 3 = 5(실제로는 8개가 생겼다). copy_failed 는 줄 수 그대로(표시용), create_failed 는 mknod 실패 3.
    assert (s["files"], s["bytes"], s["copied_items"]) == (8, 7, 8)
    assert (s["copy_failed"], s["create_failed"]) == (3, 3)
    assert s["reason"] == "sync_items_failed"
    assert parsers.parse_dsync_counts(DSYNC_EXEC_MKNOD_FAILED) == (8, 7)
    # 대조: mknod 은 됐는데 데이터 복사가 실패한 파일(최종 Items 에 들어 있다)은 뺀다 -- 실 표본 DSYNC_EXEC_ITEMS_FAILED 의 2 − 1.
    assert parsers.parse_dsync_counts(_split(DSYNC_EXEC_ITEMS_FAILED)[0]) == (1, 3)


def test_dsync_create_failures_count_and_fail_the_run_without_a_scan_reason():
    # mkdir()·symlink() 실패(mfu_flist_copy.c 1029·1202)는 예전엔 아무 개수에도 안 들어 사유가 없었다(execution_failed). walk
    # 오류가 함께 있으면 source_scan_incomplete 로 원인을 가렸다(d192 리뷰 portal-jobs-17).
    out = (f"{_TS6}[0] [dsync.c:3884] ERROR: Errors detected walking source path, delete option disabled\n"
           f"{_TS6}[0] [mfu_flist_copy.c:1029] ERROR: Create `/t/dst/sub' mkdir() failed (errno=13 Permission denied)\n"
           f"{_TS6}[0] [mfu_flist_copy.c:1202] ERROR: Create `/t/dst/ln' symlink() failed, (errno=13 Permission denied)\n"
           f"{_TS6}Seconds: 0.010\n{_TS6}Items: 4\n{_TS6}  Directories: 1\n{_TS6}  Files: 3\n{_TS6}  Links: 0\n"
           f"{_TS6}Data: 3.000 B (3 bytes)\n")
    s = _build_summary("dsync", out, 1, "/x")
    assert s["create_failed"] == 2 and s["copy_failed"] == 0
    assert s["copied_items"] == 4                       # 최종 Items 는 이미 만들기 실패를 뺀 수다 -- 다시 빼지 않는다
    assert s["scan_incomplete"] is True and s["delete_disabled"] is True and "scan_errors" not in s
    assert [x["op"] for x in s["failure_samples"]] == ["mkdir", "symlink"]
    assert s["reason"] == "sync_items_failed"


def test_dsync_final_block_bytes_are_not_the_copy_attempt_total():
    # "Copy data: 6 bytes" 는 복사 **시도** 합(열지 못한 파일 몫 포함)이다 -- 최종 블록의 Data(3 bytes)가 실제로 쓴 바이트.
    out = _split(DSYNC_EXEC_ITEMS_FAILED)[0]
    assert "Copy data: 6.000 B (6 bytes)" in out
    assert parsers.parse_sync_counts(out) == (2, 3)
    no_final = out.split("[2026-10-27T13:52:53] Started: ")[0]
    assert parsers.parse_sync_counts(no_final) == (None, None)       # 예전: (2, 6) -- 계획 Items 와 시도 바이트


def test_dsync_delete_ok():
    assert _summary("dsync", DSYNC_EXEC_DELETE_OK, DSYNC_EXEC_DELETE_OK_RC) == {
        "returncode": 0, "files": 1, "bytes": 2, **_head("dsync", False),
        "copy_failed": 0, "create_failed": 0, "copied_items": 1, "copied_bytes": 2, "removed": 3, "skipped_special": 1,
        "tool_errors": 0}


def test_dsync_direct_copy_ok():
    s = _summary("dsync", DSYNC_EXEC_DIRECT_BAD_BUFSIZE, DSYNC_EXEC_DIRECT_BAD_BUFSIZE_RC)
    assert (s["files"], s["bytes"], s["copied_items"]) == (1, 100000, 1)
    assert "reason" not in s


def test_dsync_write_error_with_enospc_is_destination_full():
    # 합성(mfu_flist_copy.c:1834 형식): 쓰기 오류는 「Failed to」 가 아니어도 errno 를 가진 실패 줄이다 -- 소스 경로를 싣는다.
    out = ("[2026-10-27T13:00:00] [0] [mfu_flist_copy.c:1834] ERROR: Write error when copying from `/s/big' to "
           "`/d/big' (errno=28 No space left on device)\n"
           "[2026-10-27T13:00:00] [0] [mfu_flist_copy.c:2256] ERROR: Failed to copy `/s/big' to `/d/big'\n")
    s = _build_summary("dsync", out, 1, "/x")
    assert s["failure_samples"] == [{"op": "write", "path": "/s/big", "errno": 28}]
    assert s["copy_failed"] == 1 and s["reason"] == "destination_full"


def test_dsync_walk_errors_disable_delete():
    # dsync 는 walk 오류 개수를 찍지 않는다 -- 지어낸 「1」(예전 scan_errors=1) 대신 참·거짓(d192 D7). walk 오류는 종료 코드를
    # 바꾸지 않으므로(dsync.c walk_rc 는 delete 만 끈다) 그것만으로는 사유가 없다 -- 예전엔 source_scan_incomplete 였다(D2).
    out = ("[2026-10-27T13:00:00] [0] [dsync.c:3884] ERROR: Errors detected walking source path, delete option disabled\n")
    d = parse_tool_detail("dsync", out, "", False)
    assert d["scan_incomplete"] is True and d["delete_disabled"] is True
    assert "scan_errors" not in d
    assert _derive_reason("dsync", d) is None
    assert _derive_reason("dsync", d, dryrun=True) is None


# ---- drm ----

def test_drm_partial_failure():
    s = _summary("drm", DRM_EXEC_PARTIAL, DRM_EXEC_PARTIAL_RC)
    assert (s["files"], s["removed"], s["remove_failed"]) == (4, 4, 5)
    assert s["failure_samples"] == [
        {"op": "unlink", "path": "/tmp/smp/rm/t/root/r2", "errno": 13},
        {"op": "unlink", "path": "/tmp/smp/rm/t/root/r1", "errno": 13},
        {"op": "unlink", "path": "/tmp/smp/rm/t/root/r3", "errno": 13},
        {"op": "rmdir", "path": "/tmp/smp/rm/t/root", "errno": 39},
        {"op": "rmdir", "path": "/tmp/smp/rm/t", "errno": 13}]
    assert s["tool_errors"] == 6
    assert s["reason"] == "rm_items_failed"


def test_drm_ok():
    assert _summary("drm", DRM_EXEC_OK, DRM_EXEC_OK_RC) == {
        "returncode": 0, "files": 3, "bytes": None, **_head("drm", False), "removed": 3, "tool_errors": 0}


def test_drm_preview_target_items():
    out = DRM_EXEC_OK.split("[2026-10-27T13:52:58] Removing")[0] + "\nType=DIR File=/tmp/smp/rm2\n\n"
    assert _build_summary("drm", out, 0, "/x", dryrun=True) == {
        "returncode": 0, "files": 3, "bytes": None, **_head("drm", True), "target_items": 3, "tool_errors": 0}


# ---- 사유 규칙(spec §2 표 순서) ----

@pytest.mark.parametrize("tool,detail,want", [
    # 1. 중단은 무엇보다 먼저
    ("dsync", {"aborted": True, "copy_failed": 3}, "tool_aborted"),
    # 2·3. 가득 참·읽기 전용(errno 또는 nsync stop_cause) -- sync 도구만(d192 D3)
    ("dsync", {"failure_samples": [{"op": "write", "path": "a", "errno": 122}], "copy_failed": 1}, "destination_full"),
    ("dsync", {"failure_samples": [{"op": "write", "path": "a", "errno": 30}], "copy_failed": 1},
     "destination_read_only"),
    ("nsync", {"stop_cause": "destination_full", "failures": {"total": 1, "by_op": {"copy": 1}}}, "destination_full"),
    ("nsync", {"stop_cause": "destination_read_only"}, "destination_read_only"),
    # rm 에는 「목적지」가 없고 drm 은 멈추지 않는다 -- 읽기 전용·가득 참 errno 도 rm_items_failed 다(예전 destination_read_only)
    ("drm", {"failure_samples": [{"op": "unlink", "path": "a", "errno": 30}], "remove_failed": 1}, "rm_items_failed"),
    ("drm", {"failure_samples": [{"op": "unlink", "path": "a", "errno": 28}], "remove_failed": 1}, "rm_items_failed"),
    # 4. max-errors
    ("nsync", {"stop_cause": "max_errors", "failures": {"total": 3, "by_op": {"copy": 3}}}, "sync_stopped_max_errors"),
    # 5. 타입 교체만 -- 문구(「삭제 옵션 꺼짐, 삭제를 켜면 바뀝니다」)가 참일 때만(d192 D1)
    ("nsync", {"type_change_refused": 2, "type_change_needs_delete": True,
               "failures": {"total": 2, "by_op": {"replace-dir": 2}}}, "sync_type_change_refused"),
    # --delete 인데 재귀 제거가 ENOTEMPTY(동시에 생긴 항목) -- nsync 는 「--delete 없으면」 줄을 찍지 않는다
    ("nsync", {"type_change_refused": 2, "failures": {"total": 2, "by_op": {"replace-dir": 2}}}, "sync_items_failed"),
    ("nsync", {"type_change_refused": 1, "type_change_needs_delete": True,
               "failures": {"total": 2, "by_op": {"replace-dir": 1, "copy": 1}}}, "sync_items_failed"),
    # type-change(파일 -> 디렉토리) 실패는 「삭제를 켜면 바뀐다」가 아니다
    ("nsync", {"failures": {"total": 1, "by_op": {"type-change": 1}}}, "sync_items_failed"),
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 0, "_type_change_dir_refused": 1,
               "_remove_errnos": [39]}, "sync_type_change_refused"),
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 0, "_type_change_dir_refused": 1,
               "_remove_errnos": [17]}, "sync_type_change_refused"),
    # 근거(내부 키)가 없으면 -- 예전엔 이것만으로 「타입 교체만」이었다
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 0}, "sync_items_failed"),
    # 막힌 것이 디렉토리 + delete 꺼짐이 아니다(비 디렉토리 생존자, delete 켜짐)
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 0, "_type_change_dir_refused": 0,
               "_remove_errnos": [39]}, "sync_items_failed"),
    ("dsync", {"type_change_refused": 2, "remove_failed": 2, "copy_failed": 0, "_type_change_dir_refused": 1,
               "_remove_errnos": [39]}, "sync_items_failed"),
    # 권한 오류(EPERM·EACCES) -- 삭제를 켜도 바뀌지 않는다
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 0, "_type_change_dir_refused": 1,
               "_remove_errnos": [1]}, "sync_items_failed"),
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 0, "_type_change_dir_refused": 1,
               "_remove_errnos": [13, 39]}, "sync_items_failed"),
    # 다른 실패가 있으면
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 1, "_type_change_dir_refused": 1,
               "_remove_errnos": [39]}, "sync_items_failed"),
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 0, "create_failed": 1,
               "_type_change_dir_refused": 1, "_remove_errnos": [39]}, "sync_items_failed"),
    ("dsync", {"type_change_refused": 1, "remove_failed": 3, "copy_failed": 0, "_type_change_dir_refused": 1,
               "_remove_errnos": [39]}, "sync_items_failed"),
    ("dsync", {"type_change_refused": 1, "remove_failed": 1, "copy_failed": 0, "scan_incomplete": True,
               "_type_change_dir_refused": 1, "_remove_errnos": [39]}, "sync_items_failed"),
    # 6. 소스 스캔 오류만(nsync) -- delete 가 실제로 꺼졌고(그 줄) 스캔 오류가 전부 소스 쪽일 때만(d192 D2)
    ("nsync", {"scan_errors": 2, "source_scan_errors": 2, "delete_disabled": True,
               "failures": {"total": 2, "by_op": {"scan": 2}}}, "source_scan_incomplete"),
    # 목적지 쪽 읽기 오류(--delete 는 꺼지지 않았다 -- 삭제가 돌았다)
    ("nsync", {"scan_errors": 2, "failures": {"total": 2, "by_op": {"scan": 2}}}, "sync_items_failed"),
    # 소스 1 + 목적지 1
    ("nsync", {"scan_errors": 2, "source_scan_errors": 1, "delete_disabled": True,
               "failures": {"total": 2, "by_op": {"scan": 2}}}, "sync_items_failed"),
    ("nsync", {"scan_errors": 1, "source_scan_errors": 1, "delete_disabled": True,
               "failures": {"total": 2, "by_op": {"scan": 1, "copy": 1}}}, "sync_items_failed"),
    # dsync 의 walk 오류는 종료 코드를 바꾸지 않는다 -- 규칙 6 은 dsync 에 없다
    ("dsync", {"scan_incomplete": True, "delete_disabled": True, "copy_failed": 0}, None),
    ("dsync", {"scan_incomplete": True, "copy_failed": 2}, "sync_items_failed"),
    # 7. 그 밖의 항목 실패(만들기 실패 포함 -- d192 D7)
    ("dsync", {"remove_failed": 2, "copy_failed": 0}, "sync_items_failed"),
    ("dsync", {"copy_failed": 0, "create_failed": 1}, "sync_items_failed"),
    ("dsync", {"scan_incomplete": True, "copy_failed": 0, "create_failed": 1}, "sync_items_failed"),
    # 8. drm
    ("drm", {"remove_failed": 5, "removed": 4}, "rm_items_failed"),
    # 없음 -- 0 은 실패가 아니다, 모름(키 없음)도 아니다
    ("drm", {"remove_failed": 0, "removed": 4}, None),
    ("dsync", {"copy_failed": 0, "tool_errors": 1}, None),
    ("nsync", {}, None),
    ("dscan", {}, None),
    ("dscan", {"aborted": True}, "tool_aborted"),
])
def test_reason_rules(tool, detail, want):
    assert _derive_reason(tool, detail) == want


def test_preview_failure_gets_only_preview_reasons():
    # 미리보기는 아무것도 바꾸지 않는다 -- 「나머지는 반영됐습니다」류 실행 사유를 붙이지 않는다. nsync 스캔 오류(소스든 목적지든
    # -- 문구는 쪽을 말하지 않는다)는 미리보기 전용 사유(preview_scan_incomplete), 그 밖의 실패 줄은 사유 없음(preview_failed).
    assert _derive_reason("nsync", {"scan_errors": 1, "failures": {"total": 1, "by_op": {"scan": 1}}},
                          dryrun=True) == "preview_scan_incomplete"
    assert _derive_reason("nsync", {"failures": {"total": 1, "by_op": {"scan": 1}}}, dryrun=True) == "preview_scan_incomplete"
    assert _derive_reason("nsync", {"failures": {"total": 2, "by_op": {"copy": 2}}}, dryrun=True) is None
    assert _derive_reason("drm", {"scan_errors": 1}, dryrun=True) is None
    assert _derive_reason("dsync", {"aborted": True}, dryrun=True) == "tool_aborted"
    # dsync walk 오류는 종료 코드를 바꾸지 않는다 -- 미리보기 실패의 원인이 아니다(d192 D2: 예전엔 preview_scan_incomplete).
    assert _derive_reason("dsync", {"scan_incomplete": True, "delete_disabled": True}, dryrun=True) is None
    assert _derive_reason("dsync", {"scan_errors": 1}, dryrun=True) is None


def test_no_reason_on_success_even_with_failure_lines():
    # rc 0 이면 사유 키가 없다(사유는 실패한 실행의 것) -- 상세는 그대로 남는다.
    out, _ = _split(NSYNC_EXEC_ITEMS_FAILED)
    s = _build_summary("nsync", out, 0, "/x")
    assert "reason" not in s and s["failures"]["total"] == 5


# ---- 중단(ABORT) ----

def test_abort_is_detected_from_mpirun_or_mfu_abort_lines():
    # 합성(mfu_util.c mfu_abort -> stderr "ABORT: rank X on HOST: ...", mpirun 배너 "MPI_ABORT was invoked ...").
    stderr = ("ABORT: rank X on HOST: O_DIRECT requires chunk size to be integer multiple of block size 3145728 "
              "@ mfu_flist_copy.c:1730\n")
    s = _build_summary("dsync", "", 1, "/x", stderr=stderr)
    assert s["aborted"] is True and s["reason"] == "tool_aborted"
    banner = "MPI_ABORT was invoked on rank 0 in communicator MPI_COMM_WORLD\nwith errorcode -1.\n"
    assert _build_summary("nsync", "", 1, "/x", stderr=banner)["reason"] == "tool_aborted"
    # quiet 에서도 남는 유일한 흔적이다.
    s = _build_summary("nsync", "", 1, "/x", quiet=True, stderr=banner)
    assert s["aborted"] is True and s["reason"] == "tool_aborted" and s["files"] is None


def test_abort_text_inside_a_dsync_path_does_not_count():
    # dsync·drm 은 경로를 날것으로 찍는다 -- stdout 줄 한가운데의 " ABORT: " 는 이름이다.
    out = ("[2026-10-27T13:00:00] [0] [mfu_flist_remove.c:84] ERROR: Failed to unlink `/u/x ABORT: y' "
           "(errno=13 Permission denied)\n")
    assert "aborted" not in parse_tool_detail("drm", out, "", False)


def test_abort_is_never_read_from_stdout():
    # 진짜 중단 흔적(mfu_abort·mpirun 배너)은 stderr 에만 온다. 이름의 줄바꿈으로 stdout 줄 머리에 심은 "ABORT: " 가 부분 rm 을
    # tool_aborted(「direct 와 버퍼 크기 조합…」)로 바꾸던 위조 통로를 닫았다(d192 D4 -- 예전엔 stdout 줄 머리도 봤다).
    forged = _forged_error("unlink `/tmp/smp/rm/t/a", "ABORT: x", "MPI_ABORT was invoked on rank 0")
    real = "[2026-10-27T13:52:56] Removed 4 items in 0.001 seconds (3975.621 items/sec)\n"
    out = _split(DRM_EXEC_PARTIAL)[0].replace(real, forged + real)
    s = _build_summary("drm", out, 1, "/x")
    assert "aborted" not in s and s["reason"] == "rm_items_failed"
    out = _split(DSYNC_EXEC_ITEMS_FAILED)[0]
    anchor = "[2026-10-27T13:52:53] Copy data: 6.000 B (6 bytes)\n"
    s = _build_summary("dsync", out.replace(anchor, _forged_error("open input file `/tmp/smp/dsf/src/x", "ABORT: pwn")
                                            + anchor), 1, "/x")
    assert "aborted" not in s and s["reason"] == "sync_items_failed"
    assert parsers.parse_aborted("") is False and parsers.parse_aborted(None) is False


# ---- 줄 위조(dsync·drm 은 이름에 줄바꿈이 들어갈 수 있다) ----

_TS = "[2026-10-27T13:52:56] "


def _forged_error(verb_path, *forged_lines):
    """날 경로에 줄바꿈을 넣어 줄 머리부터 맞는 가짜 줄을 심은 오류 줄: 경로 = "<앞>\\n<가짜 줄들>\\nx" 이고 닫는 「'」와 errno
    꼬리는 마지막 조각("x' (errno=...)")에 붙는다 -- 가짜 줄 각각은 그 자체로 온전한 한 줄이다."""
    return (f"{_TS}[0] [mfu_flist_copy.c:2092] ERROR: Failed to {verb_path}\n"
            + "".join(f"{line}\n" for line in forged_lines)
            + "x' (errno=13 Permission denied)\n")


def test_forged_removed_and_remove_failed_before_the_real_summary_do_not_count_for_drm():
    # unlink 오류 줄의 경로에 가짜 요약 두 줄을 심는다 -- drm 은 요약 뒤에 경로를 찍지 않아 진짜가 마지막이다.
    forged = _forged_error("unlink `/tmp/smp/rm/t/evil",
                           f"{_TS}Removed 999 items in 0.001 seconds (1.000 items/sec)",
                           f"{_TS}[0] [mfu_flist_remove.c:761] ERROR: Remove failed for 0 of 999 items")
    real = f"{_TS}Removed 4 items in 0.001 seconds (3975.621 items/sec)\n"
    out = DRM_EXEC_PARTIAL.replace(real, forged + real)
    assert out != DRM_EXEC_PARTIAL
    s = _build_summary("drm", out, 1, "/x")
    assert (s["files"], s["removed"], s["remove_failed"]) == (4, 4, 5)
    # 대조: 진짜 줄이 없으면 가짜 Removed 가 읽힌다 -- 위 단언이 「마지막 매치」 규칙 덕분임을 고정한다.
    assert parse_tool_detail("drm", forged, "", False)["removed"] == 999


def test_forged_items_line_before_the_final_block_does_not_change_dsync_counts():
    forged = _forged_error("open input file `/tmp/smp/dsf/src/x",
                           "[2026-10-27T13:52:53] Items: 999",
                           "[2026-10-27T13:52:53] Data: 1.000 KiB (999 bytes)")
    anchor = "[2026-10-27T13:52:53] Copy data: 6.000 B (6 bytes)\n"
    out = DSYNC_EXEC_ITEMS_FAILED.replace(anchor, forged + anchor)
    assert out != DSYNC_EXEC_ITEMS_FAILED
    s = _build_summary("dsync", out, 1, "/x")
    assert (s["files"], s["bytes"], s["copied_items"], s["copy_failed"]) == (1, 3, 1, 1)


def test_dsync_removed_seen_twice_is_unknown_never_the_forged_value():
    # dsync 는 unlink 를 한 번만 부르지만 그 **뒤**에도 날 경로(복사 단계 오류)를 찍는다 -- 마지막 매치를 믿을 수 없어 둘 이상이면
    # 모름이다(지어내지 않는다).
    forged = _forged_error("open input file `/tmp/smp/ds/src/x",
                           f"{_TS}Removed 7 items in 0.001 seconds (1.000 items/sec)",
                           f"{_TS}[0] [mfu_flist_remove.c:761] ERROR: Remove failed for 0 of 7 items")
    anchor = "[2026-10-27T13:52:52] Copy data: 4.000 B (4 bytes)\n"
    out = DSYNC_EXEC_TYPE_CHANGE.replace(anchor, forged + anchor)
    d = parse_tool_detail("dsync", out, "", False)
    assert "removed" not in d and "remove_failed" not in d
    before = DSYNC_EXEC_TYPE_CHANGE.replace("[2026-10-27T13:52:52] Removing 1 items\n",
                                            "[2026-10-27T13:52:52] Removing 1 items\n" + forged)
    d = parse_tool_detail("dsync", before, "", False)
    assert "removed" not in d


def test_forged_remove_failed_after_the_real_one_is_ignored():
    # 진짜 「Remove failed」는 Removed 바로 다음 줄이다 -- 그 뒤(복사 단계 오류 경로)의 같은 모양은 첫 매치를 앞지르지 못한다.
    forged = _forged_error("open input file `/tmp/smp/ds/src/x",
                           f"{_TS}[0] [mfu_flist_remove.c:761] ERROR: Remove failed for 0 of 1 items")
    anchor = "[2026-10-27T13:52:52] Copy data: 4.000 B (4 bytes)\n"
    d = parse_tool_detail("dsync", DSYNC_EXEC_TYPE_CHANGE.replace(anchor, forged + anchor), "", False)
    assert (d["removed"], d["remove_failed"]) == (0, 1)


def test_forged_remove_failed_before_the_real_removed_line_is_ignored():
    # 진짜 실패가 없는 drm 실행(rc 0)에 「Remove failed」를 Removed **앞**에 심어도 받지 않는다 -- 마지막 Removed 뒤만 본다.
    forged = _forged_error("unlink `/tmp/smp/rm2/evil",
                           f"{_TS}[0] [mfu_flist_remove.c:761] ERROR: Remove failed for 3 of 3 items")
    real = "[2026-10-27T13:52:58] Removed 3 items in 0.001 seconds (4094.183 items/sec)\n"
    out = DRM_EXEC_OK.replace(real, forged + real)
    assert out != DRM_EXEC_OK
    d = parse_tool_detail("drm", out, "", False)
    assert d["removed"] == 3 and "remove_failed" not in d


def test_remove_failed_without_a_removed_line_is_not_accepted():
    out = f"{_TS}[0] [mfu_flist_remove.c:761] ERROR: Remove failed for 3 of 9 items\n"
    d = parse_tool_detail("drm", out, "", False)
    assert "remove_failed" not in d and "removed" not in d


def test_summary_lines_must_start_at_the_line_head():
    # 앵커(MFU 접두사) 없는 같은 문구는 경로 텍스트다.
    out = ("[2026-10-27T13:00:00] Requested target: /x/Planned actions: copy=999 Completed actions: copy=999\n"
           "[2026-10-27T13:00:00] Walking /y/Removed 9 items in 0.1 seconds (x)\n")
    assert parse_tool_detail("nsync", out, "", False) == {}
    assert parse_tool_detail("drm", out, "", False) == {"tool_errors": 0}
    # 경로 한가운데에 접두사째 들어 있어도(앞에 다른 글이 있으면) 줄 머리가 아니다.
    mid = "[2026-10-27T13:00:00] Walking /u/[2026-10-27T13:00:00] Removed 9 items in 0.1 seconds (x)\n"
    assert parsers.parse_rm_counts(mid) == (None, None)


# ---- 모양: ASCII·샘플 상한·경로 길이 ----

def test_summary_is_ascii_json_and_samples_are_bounded():
    # 실패 줄 15개(한글·replace 문자·긴 경로 포함) -> 샘플 10개, 경로 256자. 파일은 ASCII(제어면 read 는 strict UTF-8).
    long_name = "가" * 300
    lines = [f"{_TS}[0] [mfu_flist_remove.c:84] ERROR: Failed to unlink `/u/{long_name}{i}' (errno=13 Permission denied)"
             for i in range(15)]
    lines.append(f"{_TS}[0] [mfu_flist_remove.c:84] ERROR: Failed to unlink `/u/�' (errno=0 Success)")
    lines.append(f"{_TS}Removed 1 items in 0.001 seconds (1.000 items/sec)")
    lines.append(f"{_TS}[0] [mfu_flist_remove.c:761] ERROR: Remove failed for 16 of 17 items")
    s = _build_summary("drm", "\n".join(lines) + "\n", 1, "/x")
    text = json.dumps(s)
    assert text.isascii()
    back = json.loads(text)
    assert len(back["failure_samples"]) == 10
    assert all(len(x["path"]) == 256 for x in back["failure_samples"])
    assert back["failure_samples"][0]["path"].startswith("/u/가가")
    assert back["remove_failed"] == 16 and back["reason"] == "rm_items_failed"


def test_nsync_samples_are_capped_at_ten():
    listed = "".join(f"[2026-10-27T13:00:00]   failed copy `f{i:02d}`: Permission denied (errno 13)\n" for i in range(15))
    out = ("[2026-10-27T13:00:00] [0] [nsync.c:897] ERROR: Failed items: 15 (copy 15); listed below: 15\n" + listed)
    d = parse_tool_detail("nsync", out, "", False)
    assert d["failures"] == {"total": 15, "by_op": {"copy": 15}}
    assert [x["path"] for x in d["failure_samples"]] == [f"f{i:02d}" for i in range(10)]


def test_quiet_never_reports_known_zero_counts():
    # -q 면 ERROR 줄도 없다 -- 「복사 실패 0」「도구 오류 0」은 아는 0 이 아니라 모름이다(키를 싣지 않는다).
    s = _build_summary("dsync", "", 1, "/x", quiet=True)
    assert s == {"returncode": 1, "files": None, "bytes": None, **_head("dsync", False), "quiet": True}
    assert "copy_failed" in _build_summary("dsync", "", 1, "/x")       # 대조: quiet 가 아니면 0 은 아는 값이다


def test_errno_zero_is_unknown():
    out = "[2026-10-27T13:00:00]   failed copy `a`: unknown error (errno 0)\n"
    assert parse_tool_detail("nsync", out, "", False)["failure_samples"] == [{"op": "copy", "path": "a", "errno": None}]


def test_huge_numbers_are_unknown_not_overflowing_the_bigint_columns():
    out = f"{_TS}Removed {2 ** 64} items in 0.001 seconds (1.000 items/sec)\n"
    assert _build_summary("drm", out, 0, "/x")["files"] is None


# ---- fail-soft: 상세가 터지면 핵심 3키만 ----

def test_detail_parser_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(parsers, "_nsync_detail", boom)
    assert parse_tool_detail("nsync", NSYNC_EXEC_OK, "", False) is None


def test_build_summary_falls_back_to_core_keys_when_detail_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    out, err = _split(NSYNC_EXEC_ITEMS_FAILED)
    monkeypatch.setattr(parsers, "_nsync_detail", boom)
    # 개수 파서는 따로 돈다 -- 핵심 값은 살고 상세·사유는 없다.
    assert _build_summary("nsync", out, 1, "/x", stderr=err) == {"returncode": 1, "files": 23, "bytes": 100}
    monkeypatch.setattr(runner, "parse_tool_detail", boom)
    assert _build_summary("nsync", out, 1, "/x", stderr=err) == {"returncode": 1, "files": 23, "bytes": 100}


# ---- 러너: 사유 마커 + 도구 출력 꼬리(launcher 로그) ----

class _R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class _Rec:
    def __init__(self, rc, stdout, stderr=""):
        self.writes, self.ran = {}, []
        self._tool = _R(rc, stdout, stderr)

    def write_text(self, path, content, *, append=False):
        self.writes[path] = content

    def run(self, command):
        self.ran.append(command)
        if command[0] == "getent":
            return _R(0, "10.0.0.1   w\n")
        if command[0] in ("ssh", "sh", "chown"):
            return _R(0)
        return self._tool


def _env(tool, argv):
    return {"DMS_JR_TOOL": tool, "DMS_JR_PROCESS_COUNT": "1", "DMS_JR_PROCESSES_PER_NODE": "1", "DMS_JR_UID": "10001",
            "DMS_JR_GID": "10000", "DMS_JR_USERNAME": "alice", "DMS_JR_ARTIFACT_DIR": "/art",
            "DMS_JR_SOURCE_NODES": json.dumps(["w"]), "DMS_JR_DEST_NODES": json.dumps(["w"]),
            "DMS_JR_ARGV": json.dumps(argv)}


def _wait(role=None):
    return ["w"], "/tmp/hostfile"


def _run(tool, sample, rc, argv=("/c/m/a", "/c/m/b")):
    out, err = _split(sample)
    rec = _Rec(rc, out, err)
    got = run_job(_env(tool, list(argv)), run=rec.run, write_text=rec.write_text, read_text=lambda p: "",
                  sleep=lambda s: None, wait_hostfile=_wait)
    return got, rec


def _after_mpirun(stdout_text):
    lines = stdout_text.splitlines()
    ready = max(i for i, line in enumerate(lines) if line.startswith("DMS_JR_WORKER_READY"))
    return lines[ready + 1:]


def test_failure_prints_the_reason_marker_first_then_the_tool_output(capsys):
    from dms.execution_manifests import parse_execution_reason
    rc, rec = _run("nsync", NSYNC_EXEC_ITEMS_FAILED, NSYNC_EXEC_ITEMS_FAILED_RC)
    assert rc == 1
    out = capsys.readouterr().out
    after = _after_mpirun(out)
    assert after[0] == "DMS_EXEC_REASON=sync_items_failed"          # mpirun 뒤 첫 출력
    tool_out, tool_err = _split(NSYNC_EXEC_ITEMS_FAILED)
    n_out, n_err = len(tool_out.splitlines()), len(tool_err.splitlines())
    assert after[1] == f"DMS_JR_TOOL_OUTPUT (stdout.log 마지막 {n_out}줄)"
    assert after[2:2 + n_out] == ["  | " + line for line in tool_out.splitlines()]
    assert after[2 + n_out] == f"DMS_JR_TOOL_OUTPUT (stderr.log 마지막 {n_err}줄)"
    assert after[3 + n_out:] == ["  | " + line for line in tool_err.splitlines()]
    # 스테퍼가 launcher 로그에서 승격하는 값 그대로다(execution_manifests._parse_marker).
    assert parse_execution_reason([("launcher", out, None)]) == "sync_items_failed"
    # summary 에도 같은 사유
    assert json.loads(rec.writes["/art/summary.json"])["reason"] == "sync_items_failed"


def test_success_prints_no_marker_and_no_excerpt(capsys):
    rc, _ = _run("nsync", NSYNC_EXEC_OK, NSYNC_EXEC_OK_RC)
    assert rc == 0
    out = capsys.readouterr().out
    assert "DMS_EXEC_REASON=" not in out and "DMS_JR_TOOL_OUTPUT" not in out


def test_failure_without_a_derived_reason_echoes_output_but_no_marker(capsys):
    # quiet 실패: 사유를 정할 근거가 없다 -- 마커 없이(스테퍼가 execution_failed 로 접는다) 출력 꼬리만.
    rc, rec = _run("nsync", NSYNC_EXEC_QUIET_FAILED, NSYNC_EXEC_QUIET_FAILED_RC, argv=("--quiet", "/c/m/a", "/c/m/b"))
    assert rc == 1
    after = _after_mpirun(capsys.readouterr().out)
    assert after[0] == "DMS_JR_TOOL_OUTPUT (stdout.log 마지막 0줄)"
    assert after[1].startswith("DMS_JR_TOOL_OUTPUT (stderr.log 마지막 ")
    assert all(line.startswith("  | ") for line in after[2:])
    s = json.loads(rec.writes["/art/summary.json"])
    assert s["quiet"] is True and "reason" not in s


def test_tool_output_cannot_inject_a_reason_marker(capsys):
    # dsync 의 날 경로(줄바꿈 포함)가 마커 줄을 흉내 내도 echo 는 줄마다 "  | " 접두라 승격되지 않는다.
    from dms.execution_manifests import parse_execution_reason
    evil = (f"{_TS}[0] [mfu_flist_copy.c:2092] ERROR: Failed to open input file `/u/x\n"
            "DMS_EXEC_REASON=workers_unreachable\r\n"
            " DMS_EXEC_REASON=destination_full\x0bDMS_EXEC_REASON=destination_full' (errno=13 Permission denied)\n")
    rc, _ = _run("dsync", evil, 1)
    out = capsys.readouterr().out
    assert rc == 1
    # 도구 사유는 없다(복사 실패 줄도 Items 도 없다) -- 마커가 하나도 승격되지 않아야 한다.
    assert parse_execution_reason([("launcher", out, None)]) is None
    assert not any(line.strip().startswith("DMS_EXEC_REASON=") for line in out.split("\n"))


def test_echo_is_bounded(capsys):
    long_line = f"{_TS}" + "x" * 2000
    body = "\n".join([f"{_TS}line {i}" for i in range(200)] + [long_line]) + "\n"
    err = "\n".join(f"err {i}" for i in range(50)) + "\n"
    rec = _Rec(3, body, err)
    run_job(_env("dsync", ["/c/m/a", "/c/m/b"]), run=rec.run, write_text=rec.write_text, read_text=lambda p: "",
            sleep=lambda s: None, wait_hostfile=_wait)
    after = _after_mpirun(capsys.readouterr().out)
    assert after[0] == "DMS_JR_TOOL_OUTPUT (stdout.log 마지막 80줄)"
    echoed = after[1:81]
    assert echoed[0] == f"  | {_TS}line 121" and echoed[-1] == "  | " + long_line[:512]
    assert after[81] == "DMS_JR_TOOL_OUTPUT (stderr.log 마지막 20줄)"
    assert after[82:] == [f"  | err {i}" for i in range(30, 50)]


def test_echo_is_capped_by_utf8_bytes_keeping_the_newest_lines():
    # d192 리뷰 runner-5: 줄 수만 묶으면 80줄 × 512자(한글 ×3 바이트)가 120 KB 다. 마커·머리 줄 포함 10 KiB 안에서 최신 줄부터.
    ko = "가" * 600
    body = "\n".join(f"{_TS}{i:03d} {ko}" for i in range(100)) + "\n"
    err = "\n".join(f"err {i} " + "나" * 600 for i in range(30)) + "\n"
    report = runner._tool_failure_report("sync_items_failed", body, err)
    assert len(report.encode()) <= runner.TOOL_OUTPUT_MAX_BYTES
    lines = report.splitlines()
    assert lines[0] == "DMS_EXEC_REASON=sync_items_failed"
    out_head = lines[1]
    n_out = int(out_head.split("마지막 ")[1].rstrip("줄)"))
    assert 1 <= n_out < 80
    echoed = lines[2:2 + n_out]
    assert all(line.startswith("  | ") and len(line) == 4 + 512 for line in echoed)
    assert echoed[-1].startswith(f"  | {_TS}099 ")                       # 가장 최근 줄이 남는다(연속한 꼬리)
    assert echoed[0].startswith(f"  | {_TS}{100 - n_out:03d} ")
    err_head = lines[2 + n_out]
    n_err = int(err_head.split("마지막 ")[1].rstrip("줄)"))
    assert err_head.startswith("DMS_JR_TOOL_OUTPUT (stderr.log") and n_err >= 1
    assert lines[3 + n_out:] and lines[-1].startswith("  | err 29 ")
    assert sum(len(x.encode()) + 1 for x in lines[3 + n_out:]) <= runner.TOOL_OUTPUT_STDERR_BYTES


def test_archived_launcher_log_keeps_the_marker_and_worker_lines(capsys):
    # 파드가 사라진 뒤 포탈은 박제(stepper._diag_entry -- 파드 로그의 마지막 DIAG_TAIL_BYTES)를 보여준다. 예전엔 발췌가 그보다 커서
    # 사유 마커·DMS_JR_WORKER_READY(느린 워커 진단)가 박제에서 잘려 나갔다.
    from dms import stepper
    ko = "가" * 250
    fails = "".join(f"{_TS}[0] [mfu_flist_copy.c:2256] ERROR: Failed to copy `/s/{ko}{i}' to `/d/{ko}{i}'\n"
                    for i in range(100))
    out = fails + f"{_TS}Seconds: 0.100\n{_TS}Items: 100\n{_TS}Data: 1.000 KiB (1000 bytes)\n"
    rec = _Rec(1, out, "")
    rc = run_job(_env("dsync", ["/c/m/a", "/c/m/b"]), run=rec.run, write_text=rec.write_text, read_text=lambda p: "",
                 sleep=lambda s: None, wait_hostfile=_wait)
    assert rc == 1
    log = capsys.readouterr().out
    archived = stepper._diag_entry("launcher", log)["log"]
    assert "DMS_EXEC_REASON=sync_items_failed" in archived
    assert "DMS_JR_WORKER_READY host=w" in archived
    assert len(log.encode()) <= stepper.DIAG_TAIL_BYTES                 # 통째로 들어간다(자르지 않았다)


def test_emit_survives_an_ascii_only_stdout(monkeypatch):
    # 컨테이너 로케일이 ASCII 면 한글 머리·U+FFFD 가 UnicodeEncodeError -- 러너가 rc 대신 트레이스백으로 끝나면 안 된다.
    buf = io.BytesIO()
    monkeypatch.setattr(runner.sys, "stdout", io.TextIOWrapper(buf, encoding="ascii"))
    runner._emit("DMS_EXEC_REASON=sync_items_failed\nDMS_JR_TOOL_OUTPUT (stdout.log 마지막 1줄)\n  | �\n")
    runner.sys.stdout.flush()
    text = buf.getvalue().decode("ascii")
    assert text.startswith("DMS_EXEC_REASON=sync_items_failed\n")


def test_summary_is_written_before_the_excerpt(monkeypatch):
    # 꼬리 출력이 터져도 결과(summary.json·rc)는 이미 남아 있다.
    order = []

    class Rec(_Rec):
        def write_text(self, path, content, *, append=False):
            order.append(path)
            super().write_text(path, content, append=append)
    monkeypatch.setattr(runner, "_emit", lambda text: order.append("emit"))
    out, err = _split(DRM_EXEC_PARTIAL)
    rec = Rec(1, out, err)
    rc = run_job(_env("drm", ["/c/m/a"]), run=rec.run, write_text=rec.write_text, read_text=lambda p: "",
                 sleep=lambda s: None, wait_hostfile=_wait)
    assert rc == 1
    assert order.index("/art/summary.json") < order.index("emit")
