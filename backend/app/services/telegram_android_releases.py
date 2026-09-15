"""Telegram Android 真实发布矩阵 + REGHelp Integrity APK versionCode。

来源：APKMirror ``org.telegram.messenger`` / ``org.thunderdog.challegram``
（Play / Android 6.0+ universal 变体优先）。只收录能对上公开 APK 的
``versionName`` + Settings build + ``AndroidManifest versionCode``。

两套数不要混：

* ``app_version`` / ``app_build``：InitConnection / REGHelp Push 的显示版本
  （例 ``12.8.3 (69222)``、``0.26.5.1692``）。
* ``apk_version_code``：REGHelp ``/integrity/getToken`` 的 ``appVersionCode``，
  必须等于签名 APK 的 ``versionCode``。官方文档举例 ``85101930`` 是旧版
  Telegram，**不能**当现代包的缺省值。

官方主版近年这两套数经常相同（Play 6.0+ 变体末位多为 2）。
Telegram X 则不同：显示 ``1692``，arm64 APK ``versionCode`` 是 ``1692020``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class AndroidRelease:
    family: str
    version: str
    app_build: str
    apk_version_code: int
    min_sdk: int
    weight: int
    source: str

    @property
    def app_version(self) -> str:
        if self.family == "telegram_x":
            return f"{self.version}.{self.app_build}" if not self.version.endswith(self.app_build) else self.version
        return f"{self.version} ({self.app_build})"

    @property
    def app_version_pure(self) -> str:
        return self.version


# APKMirror org.telegram.messenger，优先 Android 6.0+ / Play Bundle。
# weight：vault 成功样本与较新稳定版更高，低 SDK 池仍能抽到 10.x/11.x。
TELEGRAM_ANDROID_RELEASES: Tuple[AndroidRelease, ...] = (
    AndroidRelease("official", "10.10.0", "45712", 45712, 23, 1, "APKMirror 10.10.0 Android 6.0+ 45712"),
    AndroidRelease("official", "10.14.5", "49452", 49452, 23, 1, "APKMirror 10.14.5 Android 6.0+ 49452"),
    AndroidRelease("official", "11.8.0", "57732", 57732, 23, 2, "APKMirror 11.8.0 Android 6.0+ 57732"),
    AndroidRelease("official", "11.13.1", "60562", 60562, 23, 2, "APKMirror 11.13.1 Android 6.0+ 60562"),
    AndroidRelease("official", "11.14.1", "61082", 61082, 23, 2, "APKMirror 11.14.1 Android 6.0+ 61082"),
    AndroidRelease("official", "12.4.1", "65102", 65102, 23, 2, "APKMirror 12.4.1 Android 6.0+ 65102"),
    AndroidRelease("official", "12.5.2", "65972", 65972, 23, 2, "APKMirror 12.5.2 Android 6.0+ 65972"),
    AndroidRelease("official", "12.6.1", "66532", 66532, 23, 2, "APKMirror 12.6.1 Android 6.0+ 66532"),
    AndroidRelease("official", "12.7.1", "67412", 67412, 23, 2, "APKMirror 12.7.1 Android 6.0+ 67412"),
    AndroidRelease("official", "12.7.2", "67432", 67432, 23, 2, "APKMirror 12.7.2 Android 6.0+ 67432"),
    AndroidRelease("official", "12.7.3", "67502", 67502, 23, 4, "APKMirror 12.7.3 Android 6.0+ 67502 / lod_user"),
    AndroidRelease("official", "12.7.3", "67509", 67509, 21, 4, "APKMirror 12.7.3 Android 5.0 later 67509 / lod_user"),
    AndroidRelease("official", "12.8.0", "69132", 69132, 23, 3, "APKMirror 12.8.0 Android 6.0+ 69132"),
    AndroidRelease("official", "12.8.1", "69162", 69162, 23, 3, "APKMirror 12.8.1 Android 6.0+ 69162"),
    AndroidRelease("official", "12.8.2", "69202", 69202, 23, 3, "APKMirror 12.8.2 Android 6.0+ 69202"),
    AndroidRelease("official", "12.8.3", "69222", 69222, 23, 5, "APKMirror 12.8.3 Android 6.0+ 69222 / lod_user 成功样本"),
    AndroidRelease("official", "12.8.3", "69229", 69229, 21, 3, "APKMirror 12.8.3 Android 5.0 later 69229"),
    AndroidRelease("official", "12.9.0", "69662", 69662, 23, 3, "APKMirror 12.9.0 Android 6.0+ 69662"),
    AndroidRelease("official", "12.9.1", "69792", 69792, 23, 3, "APKMirror 12.9.1 Android 6.0+ 69792"),
    AndroidRelease("official", "12.9.2", "69912", 69912, 23, 3, "APKMirror 12.9.2 Android 6.0+ 69912"),
    AndroidRelease("official", "12.10.0", "70312", 70312, 23, 3, "APKMirror 12.10.0 Android 6.0+ 70312"),
)

TELEGRAM_X_RELEASE = AndroidRelease(
    "telegram_x",
    "0.26.5",
    "1692",
    1692020,
    21,
    1,
    "APKMirror Telegram X 0.26.5.1692 arm64-v8a versionCode 1692020",
)

TELEGRAM_9_RELEASE = AndroidRelease(
    "telegram_9",
    "9.6.7",
    "33632",
    33632,
    23,
    1,
    "APKMirror 9.6.7 Android 6.0+ 33632（旧合成串 33219 不是 APK versionCode）",
)

# 旧指纹包 / 模板里写过、但对不上 APKMirror 显示 build 的串 → 仍解析到真实 versionCode。
_APK_VERSION_CODE_ALIASES: Dict[Tuple[str, str], int] = {
    ("9.6.7", "33219"): 33632,
}

_FAKE_OFFICIAL_BUILDS = frozenset({
    "42207", "49970", "54610", "58921", "60118", "61220", "62841",
    "63902", "64811", "65540", "66218", "68420",
})


def official_release_tuples() -> List[Tuple[str, str]]:
    """兼容旧调用方：``(version, app_build)``。"""
    return [(item.version, item.app_build) for item in TELEGRAM_ANDROID_RELEASES]


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def coerce_apk_version_code(value: Any) -> int:
    digits = _digits(value)
    if not digits:
        return 0
    try:
        parsed = int(digits)
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


def _family_from_profile(profile: Optional[Dict[str, Any]]) -> str:
    profile = profile or {}
    app_type = str(profile.get("app_type") or profile.get("key") or "").strip()
    app_name = str(profile.get("app_name") or "").strip().lower()
    lang_pack = str(profile.get("lang_pack") or "").strip().lower()
    if app_type == "telegram_x" or app_name == "tg_x" or lang_pack == "android_x":
        return "telegram_x"
    if app_type == "telegram_9":
        return "telegram_9"
    return "official"


def lookup_release(
    version: str = "",
    app_build: str = "",
    family: str = "official",
) -> Optional[AndroidRelease]:
    version = str(version or "").strip()
    app_build = _digits(app_build)
    pool: Sequence[AndroidRelease]
    if family == "telegram_x":
        pool = (TELEGRAM_X_RELEASE,)
    elif family == "telegram_9":
        pool = (TELEGRAM_9_RELEASE,)
    else:
        pool = TELEGRAM_ANDROID_RELEASES
        if version.startswith("9."):
            pool = (TELEGRAM_9_RELEASE, *TELEGRAM_ANDROID_RELEASES)
    for item in pool:
        if app_build and item.app_build == app_build:
            if not version or item.version == version or version.startswith(item.version):
                return item
        if version and not app_build and item.version == version:
            return item
    if family == "telegram_x":
        if app_build == TELEGRAM_X_RELEASE.app_build or version.startswith("0.26.5"):
            return TELEGRAM_X_RELEASE
    if family == "telegram_9" or version.startswith("9.6.7"):
        return TELEGRAM_9_RELEASE
    return None


def lookup_apk_version_code(
    version: str = "",
    app_build: str = "",
    family: str = "official",
) -> int:
    version = str(version or "").strip()
    build = _digits(app_build)
    alias = _APK_VERSION_CODE_ALIASES.get((version, build))
    if alias:
        return alias
    release = lookup_release(version, build, family)
    if release:
        return int(release.apk_version_code)
    return 0


def resolve_apk_version_code(profile: Optional[Dict[str, Any]]) -> int:
    """Integrity 只用这一处。禁止把 Telegram X 的显示 build 1692 当 versionCode。"""
    profile = profile or {}
    explicit = coerce_apk_version_code(
        profile.get("apk_version_code") if profile.get("apk_version_code") not in (None, "", 0, "0")
        else profile.get("app_version_code")
    )
    family = _family_from_profile(profile)
    version = str(profile.get("app_version_pure") or "").strip()
    app_build = _digits(profile.get("app_build"))
    if not version:
        text = str(profile.get("app_version") or "").strip()
        if "(" in text:
            version = text.split("(", 1)[0].strip()
            if not app_build:
                app_build = _digits(text.split("(", 1)[-1])
        elif family == "telegram_x" and text.startswith("0."):
            version = "0.26.5"
            if not app_build:
                app_build = _digits(text.rsplit(".", 1)[-1])
        else:
            version = text.split()[0] if text else ""
    looked_up = lookup_apk_version_code(version, app_build, family)
    if explicit and looked_up and explicit != looked_up:
        # 指纹包写过错误值时，以 APKMirror 目录为准
        return looked_up
    if explicit:
        if family == "telegram_x" and explicit == coerce_apk_version_code(app_build):
            return looked_up or TELEGRAM_X_RELEASE.apk_version_code
        return explicit
    if looked_up:
        return looked_up
    return 0


def attach_apk_version_code(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(profile or {})
    code = resolve_apk_version_code(data)
    if code:
        data["apk_version_code"] = code
    elif "apk_version_code" not in data:
        data["apk_version_code"] = 0
    return data


def pick_official_release(sdk: int, rng: Any) -> AndroidRelease:
    pool: List[AndroidRelease] = list(TELEGRAM_ANDROID_RELEASES)
    if sdk <= 30:
        pool = [item for item in pool if item.version.startswith(("10.", "11.", "12.4", "12.5", "12.6"))] or pool
    elif sdk <= 32:
        pool = [item for item in pool if not item.version.startswith("10.")] or pool
    else:
        pool = [item for item in pool if item.version.startswith("12.") and not item.version.startswith(("12.4", "12.5", "12.6"))] or pool
    weights = [max(1, item.weight) for item in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def known_official_app_versions() -> Iterable[str]:
    return (item.app_version for item in TELEGRAM_ANDROID_RELEASES)


def is_fake_official_build(app_build: Any) -> bool:
    return _digits(app_build) in _FAKE_OFFICIAL_BUILDS
