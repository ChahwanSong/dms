"""Volcano 매니페스트 + 도구 명령 빌더. 전부 순수 함수 — 실제 제출은 어댑터(Task 6)."""

_SYNC_BOOL_FLAGS = {"delete": "--delete", "contents": "--contents",
                    "direct": "--direct", "open_noatime": "--open-noatime",
                    "quiet": "--quiet"}
_SYNC_VALUE_FLAGS = {"batch_files": "--batch-files", "bufsize": "--bufsize",
                     "chmod": "--chmod", "chown": "--chown"}
_RM_BOOL_FLAGS = {"stat": "--stat", "lite": "--lite", "quiet": "--quiet"}


def render_tool_flags(tool: str, options: dict) -> list[str]:
    options = options or {}
    flags: list[str] = []
    if tool in ("dsync", "nsync"):
        for key, flag in _SYNC_BOOL_FLAGS.items():
            if options.get(key) is True:
                flags.append(flag)
        for key, flag in _SYNC_VALUE_FLAGS.items():
            if key in options:
                flags.extend([flag, str(options[key])])
    elif tool == "drm":
        for key, flag in _RM_BOOL_FLAGS.items():
            if options.get(key) is True:
                flags.append(flag)
    return flags


def tool_argv(spec, *, abs_paths: dict) -> list[str]:
    if spec.tool == "dscan":
        return ["--directory", abs_paths["target"],
                "--output", "$DMS_SCAN_REPORT", "--print"]
    flags = render_tool_flags(spec.tool, spec.options)
    dry = ["--dryrun"] if spec.dryrun else []
    if spec.tool in ("dsync", "nsync"):
        return [*flags, *dry, abs_paths["source"], abs_paths["destination"]]
    # drm
    return [*flags, *dry, abs_paths["target"]]
