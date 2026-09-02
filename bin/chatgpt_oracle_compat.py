from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

SUPPORTED_VERSION = "0.18.0"
LKG_VERSION = "0.17.1"
CREATE_NO_WINDOW = 0x08000000
# Retained only to document the old pre-LKG package lineage. New work uses
# PATCHES for 0.18.0; exact historical recovery uses LKG_PATCHES for 0.17.1.
# Oracle 0.16.1 is not accepted anymore.
PATCHES_0161 = {
    "dist/src/cli/browserTabs.js": {
        "patch": "browserTabs.patch",
        "pristine": "05256692ffa9b35415346963adde5ff42aeacd78ce46dd6f484496678f5d0281",
        "patched": "9329e259f030ecb4a935fb9e368bf55074bf0afe7ed5e5a0c6206a5f2bbacee4",
        "legacy_patched": ["1a6d3b9d7044d84300f630fe669b16d9cfec3925c427cfb4c3d1291205406dab"],
        "legacy_patch": "browserTabs.pre-readiness.patch",
    },
    "dist/src/browser/chromeLifecycle.js": {
        "patch": "chromeLifecycle.patch",
        "pristine": "9eaffd8264051266581548ea9dbee1152bd94b7a6032ed0441b1ba3c11c5b5e9",
        "patched": "d852372c9c16c9a130a280001e62312542092b0c38397907897217f8af0c559d",
    },
    "dist/src/browser/recoverConversation.js": {
        "patch": "recoverConversation.patch",
        "pristine": "8c7d841bc078af20c8922ec435f62e00df7a40605583fbd89334696b3ddb386b",
        "patched": "168d665fa7c6cc0ef5094a990e94e7a3ae57f2d3bebcc5c2625cb6cff0cb89b1",
        "legacy_patched": ["650ffe9bdbbaf799510e8cacaa8ba8407322bbbb175e790a3cf7777fa14772fe"],
    },
    "dist/src/browser/profileCopy.js": {
        "patch": "profileCopy.patch",
        "pristine": "06c692861f8a4c1a8769f957b9c582426a13bf4972262c47c1f24a87b239064f",
        "patched": "71459a25b7c46f57bae6f23a5498301f6f6a1d39addf0c1cd4eee1d99b03372c",
    },
    "dist/src/cli/browserConfig.js": {
        "patch": "browserConfig.patch",
        "pristine": "2e4bbf102eed4276e105671244c5bfcdf6870ea72f304d8bb7d2ede10b8872c6",
        "patched": "52de31fff0ee71a9f9ed96400565104ff19226274335433732dfc4a932ff2e93",
    },
    "dist/src/browser/index.js": {
        "patch": "browserIndex.patch",
        "pristine": "ea7461262078363517ad16fe57ac8766f06f037782954987520375dcbfd1c7af",
        "patched": "5f7bc607dae4667ad860d2aa125c138c053190e33f206237c24f5c6aab4bf14c",
        "legacy_patched": [
            "9168df2b3e8c4d1c962d05b198ceab1a9df9e50c7573453673212905e2bc5eba",
        ],
    },
    "dist/src/browser/actions/assistantResponse.js": {
        "patch": "assistantResponse.patch",
        "pristine": "0bbc106f79c6abf253690c83794a2dab1b432378f57e16542d15cfcd5365e16d",
        "patched": "18661304c7fb545bc327876d38045818cbd23257488137836d43661be8742af4",
    },
    "dist/src/browser/actions/promptComposer.js": {
        "patch": "promptComposer.patch",
        "pristine": "db090a5fb6d13c4c88a68b5e474a53a19c3857295a64c3ba4a0eef1868d06000",
        "patched": "02874d0f2fcd0f45c2c50385893a210e2be5822e1831fa81b99944728ed1cb79",
        "legacy_patched": [
            "a4bd36940deca4fd4ca63536df863fff330e438c82d47871834d56cdc064f2b5",
            "3eb83d2b1fc0d9e097a26eca282dc36c93dac1e8485410f8c3617c826538e409",
            # Pre-downgrade build that hard-failed a run when the app mention
            # overlay or pill could not be observed.
            "446ee00d52111c6ba41eea9b60752374603bd4ae3e52985e129e9f5625dadb28",
        ],
    },
    "dist/src/browser/actions/modelSelection.js": {
        "patch": "modelSelection.patch",
        "pristine": "62351158216c0f9f81652f072413487d2db12cd20a1cf7c21575a3f3a2074573",
        "patched": "7e19a5bfd10668929d24961259c4ddedfdd8c26bc85b3ac4672c29f1f40f74fc",
    },
}

LKG_PATCHES = {
    "dist/src/browser/actions/assistantResponse.js": {
        "patch": "assistantResponse.terminal-marker-fallback.patch",
        "pristine": "0bbc106f79c6abf253690c83794a2dab1b432378f57e16542d15cfcd5365e16d",
        "patched": "e126daca4dc82d6253a3bdd840411ac5da0d1b00d9d514a66ae543b0ea5eda61",
        "legacy_patched": [
            "60e6429a44b32dd71e1ce7d3362f4b4911533e9c66a188f3ecb5fc4ac4147fd5",
            "5bdb4dbc9fd6d6ee3478d2c127a45114a0d698c92009bad77074b1839a8dfa9b",
        ],
        "legacy_patches": {
            "5bdb4dbc9fd6d6ee3478d2c127a45114a0d698c92009bad77074b1839a8dfa9b":
                "assistantResponse.terminal-marker-fallback.v1.19.6.patch",
        },
        "legacy_patch": "assistantResponse.terminal-marker-fallback.v1.19.2.patch",
    },
    "dist/bin/oracle-cli.js": {
        "patch": "oracle-cli.followup-port-binding.patch",
        "pristine": "c2b39880451c467967c5fcd0e7c632c8b9bef96b7f30bf82b023ce8253fc96c2",
        "patched": "b70e8f0a6b83da8c1728d52a9f88dc0301070d8e390c8d09a804c43fff4bbf52",
    },
    "dist/src/cli/browserConfig.js": {
        "patch": "browserConfig.followup-port-binding.patch",
        "pristine": "989f14399c8aa51913752306135e11d97e4f1c55b2baf984907f1b54959cc340",
        "patched": "9ea923aaa41dff884797c828e66b569ef9a819d7fbf04bd21c53cac257561ea2",
    },
    "dist/src/browser/actions/thinkingStatus.js": {
        "patch": "thinkingStatus.undetected-warning.patch",
        "pristine": "0297df6855ce653ada2ec8509bba71651dd3ac9b78041ecb4343c765fd47e56c",
        "patched": "f4d3585b822df2b5ed2834189ba0012168a579d205c314ff2ace82f499b0ac67",
        "legacy_patched": [
            "707bc1b582ce36c5cb816c26490d8e204238b3948f794ec4715f69a27175b094",
        ],
        "legacy_patch": "thinkingStatus.undetected-warning.v1.19.2.patch",
    },
    "dist/src/browser/actions/navigation.js": {
        "patch": "navigation.resumed-hydration-recheck.patch",
        "pristine": "e1383e1566134173c0799a2af82ba2feeb971d1f2a3df81dc61c6827aa6e3adb",
        "patched": "ca039139c996acf1a22629bbcb398dff7952ce12c267de9334e1c01236c0aa8c",
    },
    "dist/src/cli/followup.js": {
        "patch": "oracle-followup.archived-parent.patch",
        "pristine": "d6082bf55cd997f07c9718dfe7ca2ba54520c2e414fbd17f21b9225b4a9b57b3",
        "patched": "0545cd02472c59609ee83c531361ca82ebd3f98d3b5afcf50ff0f035d496f952",
    },
    "dist/src/browser/actions/archiveConversation.js": {
        "patch": "archiveConversation.unarchive-followup.patch",
        "pristine": "88ab449b818bfe4bb8abe6824647feedcc617adc8af10b055278bd134fe21f82",
        "patched": "45c330d82108e0e9c2a857350f2e378e48751d89cc4db19920f75f30edb17867",
        "legacy_patched": [
            "edd00f89b0974696ccee8e0d844d7992871f34074cea7481619957937b391221",
        ],
        "legacy_patch": "archiveConversation.unarchive-followup.v1.18.4.patch",
    },
    "dist/src/browser/index.js": {
        "patch": "browserIndex.unarchive-followup.patch",
        "pristine": "335f29c8864399cf2795333e4da8b87bc1b3591c30862eb9e82ea12cd3b37d11",
        "patched": "2b2f90b0bcd4d9e03e64902b8fea061b76fdf63d632dd7a8d577031a26a687b3",
        "legacy_patched": [
            "000bdacec15e0d933e95bdd398bf1e758f4eb979f38e0f506665b5462efab73e",
        ],
        "legacy_patch": "browserIndex.unarchive-followup.v1.18.4.patch",
    },
    "dist/src/cli/browserTabs.js": {
        "patch": "browserTabs.live-terminal-timeout.patch",
        "pristine": "05256692ffa9b35415346963adde5ff42aeacd78ce46dd6f484496678f5d0281",
        "patched": "9329e259f030ecb4a935fb9e368bf55074bf0afe7ed5e5a0c6206a5f2bbacee4",
    },
    "dist/src/browser/recoverConversation.js": {
        "patch": "recoverConversation.copy-profile.patch",
        "pristine": "d7e39d21acf07e6d227e761944519e11cd8d93930629cc87555d7de75a42d1ca",
        "patched": "31a17e1a2c3dccddf09db42b290fad517638b88c9a659e9fb5dba7307c588a61",
    },
    "dist/src/browser/profileCopy.js": {
        "patch": "profileCopy.windows-native.patch",
        "pristine": "06c692861f8a4c1a8769f957b9c582426a13bf4972262c47c1f24a87b239064f",
        "patched": "71459a25b7c46f57bae6f23a5498301f6f6a1d39addf0c1cd4eee1d99b03372c",
    },
    "dist/src/browser/config.js": {
        "patch": "browserConfig.copy-profile-windows.patch",
        "pristine": "aa4cd86b70217d08a3178c52c20eff621b553ef18b8d1adff9b930ad8e9c3b4a",
        "patched": "9d2dbde2801ab39be9d81f2e84646592ce45063df571c480b09475a2861769c5",
    },
    "dist/src/browser/actions/thinkingTime.js": {
        "patch": "thinkingTime.strict.patch",
        "pristine": "508f1fbc175b82e6bfd4c978da6199306800615f432e28d7721c155c402795ca",
        "patched": "f2244ec9e113cffbae073673c9adc3cfc70adb58a4fc64b241f85700ff3def8c",
        "legacy_patched": [
            "536571fccc3f8137bfbf0ea96dfd827f1eabdaf92f93fe7cff92af242ef01d53",
            "fe6db3c1d48ccf7eff212dab7e69a2b3c7439f44b5cc823d474aa4fbd0925151",
            "ce0fa250ba4b28aeff9e3e80267b3f55bd08f7d25c9890a0eb09debcae447b8b",
            "686e80ee7480686622eab7bc8863eccdf3ad57e64f662bfcbfbc4852802c7aaa",
            "4e73e1c1d9c04e7bea7811a5e32bf17c559a2e1171581dc4cc33f48163ef28e7",
            "374f0fabd62ea82ecf359c3050995da7a3de2d791905d04742f91ebe098d910a",
            "864f8365ecbd0aef9b631f7ae61c80b3e43424dc37c34cdfd5c6e5aa06b0c1b3",
            "d8fbe1394314efaa38343539ad7be519212fd5301f74e4aa92336f6925e3b5fd",
            "9ac1cab3200fb848ca2f88c07f98b19d94c7d4ad5a9b2e578c1c5a9dee4df15f",
            "2baba20f9162eea8b4659ff42d85c26064d037bb18dd90f2022cf4764ddd710d",
            "0cb7bf4774e5507fb97682cf4e350fea03998c2a44548065bf8e9eb57fe16707",
            "b55897a9d90627b226e39e77339819e446927ffc66f78181f5c2851cbcfe5f97",
            "3f969712b184588d1f34ef4f55b439c86256d112bb0fa1688bb473b61fd3dcc3",
            "d01b1d254041c35a300d44f7b3940a89d5406bdf30027bdc7c579a074c5ca5f3",
        ],
        "legacy_patch": "thinkingTime.strict.pre-power.patch",
        "legacy_patches": {
            "fe6db3c1d48ccf7eff212dab7e69a2b3c7439f44b5cc823d474aa4fbd0925151":
                "thinkingTime.strict.broken-power.patch",
            "ce0fa250ba4b28aeff9e3e80267b3f55bd08f7d25c9890a0eb09debcae447b8b":
                "thinkingTime.strict.double-escaped-power.patch",
            "686e80ee7480686622eab7bc8863eccdf3ad57e64f662bfcbfbc4852802c7aaa":
                "thinkingTime.strict.single-escaped-power.patch",
            "4e73e1c1d9c04e7bea7811a5e32bf17c559a2e1171581dc4cc33f48163ef28e7":
                "thinkingTime.strict.regex-power.patch",
            "374f0fabd62ea82ecf359c3050995da7a3de2d791905d04742f91ebe098d910a":
                "thinkingTime.strict.compact-power.patch",
            "864f8365ecbd0aef9b631f7ae61c80b3e43424dc37c34cdfd5c6e5aa06b0c1b3":
                "thinkingTime.strict.hidden-slider.patch",
            "d8fbe1394314efaa38343539ad7be519212fd5301f74e4aa92336f6925e3b5fd":
                "thinkingTime.strict.pro-proof-model-bound.patch",
            "9ac1cab3200fb848ca2f88c07f98b19d94c7d4ad5a9b2e578c1c5a9dee4df15f":
                "thinkingTime.strict.null-model-menu-closed.patch",
            "2baba20f9162eea8b4659ff42d85c26064d037bb18dd90f2022cf4764ddd710d":
                "thinkingTime.strict.pre-outer-model-proof.patch",
            "0cb7bf4774e5507fb97682cf4e350fea03998c2a44548065bf8e9eb57fe16707":
                "thinkingTime.strict.pre-visible-advanced-proof.patch",
            "b55897a9d90627b226e39e77339819e446927ffc66f78181f5c2851cbcfe5f97":
                "thinkingTime.strict.pre-advanced-view-sibling.patch",
            "3f969712b184588d1f34ef4f55b439c86256d112bb0fa1688bb473b61fd3dcc3":
                "thinkingTime.strict.pre-stable-pro-proof.patch",
            "d01b1d254041c35a300d44f7b3940a89d5406bdf30027bdc7c579a074c5ca5f3":
                "thinkingTime.strict.pre-menu-scoped-proof.patch",
        },
    },
}

# The current contract follows upstream stable 0.18.0 while retaining 0.17.1
# as the last-known-good rollback and exact legacy-recovery contract. Patches
# reused from 0.17.1 are byte-gated against the published 0.18.0 payload; the
# changed follow-up port/timeout and Windows copy-profile hunks are rebased in
# the 0.18.0 directory.
PATCHES = {
    "dist/src/sessionManager.js": {
        "patch": "sessionManager.windows-atomic-rename-retry.patch",
        "pristine": "c9442c359117c059f1a2145042b119bd235af7695f22929b97bc014331135a69",
        "patched": "5dd851474fb123a01421dd7650d6519f7933e5e385ec1f322291542be5521694",
    },
    "dist/bin/oracle-cli.js": {
        "patch": "oracle-cli.followup-port-and-timeout.patch",
        "pristine": "6909a8fd25ff7e5459123637e90a79d72dc5733cc2af0c14220018cb663b1825",
        "patched": "9a39768943d1b0d5c737d172b1a8a0815c80d002fe73a18b0d84af1bfc213b43",
    },
    "dist/src/cli/browserConfig.js": {
        "patch": "browserConfig.followup-port-binding.patch",
        "pristine": "52ddb9d0289849301f83863ed0b5209b8d9f071358e7784fcf4a5c8724b1c147",
        "patched": "67c1230eb37cbc4d638814bab280402081b2d5baa239ed728e32c5140c8503b1",
    },
    "dist/src/browser/config.js": {
        "patch": "browserConfig.copy-profile-windows.patch",
        "pristine": "956eff0b47da8bc35abb940b37c7f55e64177733cc668931daf53fb444e8f9cb",
        "patched": "3c24dbb5fb78e56a069103bbdb6dfe0d4f394215d9d1223a6f0b541c867b4b4d",
    },
    "dist/src/browser/chromeLifecycle.js": {
        "patch": "chromeLifecycle.disable-session-crash-bubble.patch",
        "pristine": "312b45c44d4cd69a3a057e7bd1584b58182b4b37bc88f6ce6c7d11e216267c81",
        "patched": "f3b405464515e858c9f773d67fa0e94bca07dadff8ea49caa7859ad37e730ff7",
    },
    "dist/src/browser/actions/thinkingTime.js": {
        "patch": "thinkingTime.gpt56-pro-power-slider.patch",
        "pristine": "3d9d06b08417bca3b2d646eb4d46887d26c5de7c068d1e995c73b6b6e2f61199",
        "patched": "1aa1a216f71e1213c2056efb0db4c4de7c2b2c505311e1be98c2b6a2784521dd",
        "legacy_patched": [
            "978f754ba4011957790530474d27d629a8d353dd449f8e2636e02a9abd27b81a",
            "a19ce77fe57b4fa1a290e130da323377ed69b6e51b1ad133b1ab5355ead59345",
        ],
        "legacy_patches": {
            "a19ce77fe57b4fa1a290e130da323377ed69b6e51b1ad133b1ab5355ead59345":
                "thinkingTime.gpt56-pro-power-slider.pre-aria-range.patch",
        },
        "legacy_patch": "thinkingTime.gpt56-pro-power-slider.v1.20.15.patch",
    },
    "dist/src/browser/actions/assistantResponse.js": {
        "patch": "../0.17.1/assistantResponse.terminal-marker-fallback.patch",
        "pristine": "93d2465ed7dce43d8093a91bada7656bc9ba7ba3729d2fcc43229fa8aa6e36de",
        "patched": "7a4f3f4f3daa04d4db9c3d189f6da82827f5e1e256f0325761d5aa379e839400",
    },
    "dist/src/browser/actions/thinkingStatus.js": {
        "patch": "../0.17.1/thinkingStatus.undetected-warning.patch",
        "pristine": "0297df6855ce653ada2ec8509bba71651dd3ac9b78041ecb4343c765fd47e56c",
        "patched": "f4d3585b822df2b5ed2834189ba0012168a579d205c314ff2ace82f499b0ac67",
        "legacy_patched": [
            "707bc1b582ce36c5cb816c26490d8e204238b3948f794ec4715f69a27175b094",
        ],
        "legacy_patch": "../0.17.1/thinkingStatus.undetected-warning.v1.19.2.patch",
    },
    "dist/src/browser/actions/navigation.js": {
        "patch": "../0.17.1/navigation.resumed-hydration-recheck.patch",
        "pristine": "e1383e1566134173c0799a2af82ba2feeb971d1f2a3df81dc61c6827aa6e3adb",
        "patched": "ca039139c996acf1a22629bbcb398dff7952ce12c267de9334e1c01236c0aa8c",
    },
    "dist/src/cli/browserTabs.js": {
        "patch": "../0.17.1/browserTabs.live-terminal-timeout.patch",
        "pristine": "05256692ffa9b35415346963adde5ff42aeacd78ce46dd6f484496678f5d0281",
        "patched": "9329e259f030ecb4a935fb9e368bf55074bf0afe7ed5e5a0c6206a5f2bbacee4",
    },
    "dist/src/browser/recoverConversation.js": {
        "patch": "../0.17.1/recoverConversation.copy-profile.patch",
        "pristine": "d7e39d21acf07e6d227e761944519e11cd8d93930629cc87555d7de75a42d1ca",
        "patched": "31a17e1a2c3dccddf09db42b290fad517638b88c9a659e9fb5dba7307c588a61",
    },
    "dist/src/browser/profileCopy.js": {
        "patch": "../0.17.1/profileCopy.windows-native.patch",
        "pristine": "06c692861f8a4c1a8769f957b9c582426a13bf4972262c47c1f24a87b239064f",
        "patched": "71459a25b7c46f57bae6f23a5498301f6f6a1d39addf0c1cd4eee1d99b03372c",
    },
}

# WebJjonku remains a narrower archive-verified deployment profile. The normal
# current path above is used by Windows/macOS and ordinary comprehensive runs.
SCOPED_PATCHES = {
    "webjjonku-linux": {
        "0.18.0": {
            "dist/bin/oracle-cli.js": {
                "patch": "oracle-cli.followup-timeout.patch",
                "pristine": "6909a8fd25ff7e5459123637e90a79d72dc5733cc2af0c14220018cb663b1825",
                "patched": "6635dab468730e1a1031edd07480517edc79ce55bcef29f165347f7d2680e11a",
            },
        },
    },
}

SCOPED_PACKAGE_INTEGRITIES = {
    "webjjonku-linux": {
        "0.18.0": "sha512-o8KFd66zNt36jw5zdtQAV74bgrOlJibbyvnLsVikIWDamesYtez/dIUhQ4zqtD9jkx+7A6vcP9+JgcJt0H5pOw==",
    },
}
SCOPED_NODE_MAJOR_RANGES = {"webjjonku-linux": (24, 27)}
CURRENT_NODE_MAJOR_RANGE = (24, 27)
SCOPED_ARCHIVE_MAX_FILES = 10_000
SCOPED_ARCHIVE_MAX_BYTES = 100 * 1024 * 1024
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class OracleCompatError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha512_integrity(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha512-" + base64.b64encode(digest.digest()).decode("ascii")


def _is_link_or_reparse(info: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(reparse_flag and attributes & reparse_flag)


def _safe_archive_relative(name: str) -> str | None:
    if not name or "\\" in name or "\x00" in name or any(ord(character) < 32 for character in name):
        raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive contains an unsafe path")
    raw_parts = name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or path.parts[0] != "package":
        raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive contains an unsafe path")
    if len(path.parts) == 1:
        return None
    for part in path.parts[1:]:
        if ":" in part or part.endswith((" ", ".")):
            raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive contains an unsafe path")
        device_name = part.rstrip(" .").split(".", 1)[0].upper()
        if device_name in WINDOWS_RESERVED_NAMES:
            raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive contains an unsafe path")
    return PurePosixPath(*path.parts[1:]).as_posix()


def _verify_node_runtime(minimum: int, maximum: int, *, contract: str) -> None:
    node = shutil.which("node")
    if not node:
        raise OracleCompatError(
            "ORACLE_NODE_VERSION_UNSUPPORTED",
            "Oracle compatibility requires its validated Node.js runtime",
            {"contract": contract, "required": f">={minimum} <{maximum}"},
        )
    resolved = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    value = resolved.stdout.strip().removeprefix("v")
    try:
        major = int(value.split(".", 1)[0])
    except (TypeError, ValueError):
        major = -1
    if resolved.returncode != 0 or not minimum <= major < maximum:
        raise OracleCompatError(
            "ORACLE_NODE_VERSION_UNSUPPORTED",
            "Oracle compatibility requires its validated Node.js runtime",
            {"contract": contract, "resolved": value or None, "required": f">={minimum} <{maximum}"},
        )


def _verify_scoped_node_runtime(profile: str) -> None:
    minimum, maximum = SCOPED_NODE_MAJOR_RANGES[profile]
    _verify_node_runtime(minimum, maximum, contract=f"scoped:{profile}")


def _scan_installed_package_tree(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    pending: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    total_bytes = 0
    while pending:
        directory, relative_parts = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise OracleCompatError(
                "ORACLE_PACKAGE_TREE_MISMATCH",
                "Installed Oracle package tree could not be inspected",
            ) from exc
        for entry in entries:
            item_parts = (*relative_parts, entry.name)
            relative = PurePosixPath(*item_parts).as_posix()
            try:
                item_info = entry.stat(follow_symlinks=False)
                item_path = Path(entry.path)
                canonical_item = item_path.resolve(strict=True)
            except OSError as exc:
                raise OracleCompatError(
                    "ORACLE_PACKAGE_TREE_MISMATCH",
                    "Installed Oracle package entry could not be inspected",
                    {"path": relative},
                ) from exc
            if _is_link_or_reparse(item_info) or not canonical_item.is_relative_to(root):
                raise OracleCompatError(
                    "ORACLE_PACKAGE_TREE_MISMATCH",
                    "Installed Oracle package contains a link, junction, or escaping path",
                    {"path": relative},
                )
            if stat.S_ISDIR(item_info.st_mode):
                # npm may place dependency packages below the Oracle package
                # root. Their integrity belongs to the invoking runtime lock,
                # not to the published Oracle tarball payload verified here.
                if item_parts == ("node_modules",):
                    continue
                pending.append((item_path, item_parts))
                continue
            if not stat.S_ISREG(item_info.st_mode):
                raise OracleCompatError(
                    "ORACLE_PACKAGE_TREE_MISMATCH",
                    "Installed Oracle package contains a non-file entry",
                    {"path": relative},
                )
            files[relative] = item_path
            total_bytes += int(item_info.st_size)
            if len(files) > SCOPED_ARCHIVE_MAX_FILES or total_bytes > SCOPED_ARCHIVE_MAX_BYTES:
                raise OracleCompatError(
                    "ORACLE_PACKAGE_TREE_MISMATCH",
                    "Installed Oracle package exceeds safety limits",
                )
    return files


def _verify_scoped_package_archive(
    package_root: Path,
    package_archive: Path,
    *,
    expected_integrity: str,
    contracts: dict[str, dict[str, Any]],
) -> Path:
    archive_input = package_archive.expanduser()
    try:
        archive_info = os.lstat(archive_input)
    except OSError as exc:
        raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive must be a regular file") from exc
    if _is_link_or_reparse(archive_info) or not stat.S_ISREG(archive_info.st_mode):
        raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive must be a regular file")
    if archive_info.st_size > SCOPED_ARCHIVE_MAX_BYTES:
        raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive exceeds safety limits")
    try:
        archive_bytes = archive_input.read_bytes()
    except OSError as exc:
        raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive could not be read") from exc
    actual_integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive_bytes).digest()).decode("ascii")
    if actual_integrity != expected_integrity:
        raise OracleCompatError(
            "ORACLE_PACKAGE_INTEGRITY_MISMATCH",
            "Oracle package archive integrity does not match the scoped contract",
            {"actual": actual_integrity, "expected": expected_integrity},
        )
    root_input = package_root.expanduser()
    try:
        root_info = os.lstat(root_input)
    except OSError as exc:
        raise OracleCompatError("ORACLE_PACKAGE_TREE_MISMATCH", "Installed Oracle package must be a regular directory") from exc
    if _is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise OracleCompatError("ORACLE_PACKAGE_TREE_MISMATCH", "Installed Oracle package must be a regular directory")
    root = root_input.resolve(strict=True)
    installed_files = _scan_installed_package_tree(root)
    file_count = 0
    total_bytes = 0
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as package:
            for member in package:
                relative = _safe_archive_relative(member.name)
                if member.isdir():
                    continue
                if not member.isfile() or relative is None:
                    raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive contains a non-file entry")
                if relative in seen:
                    raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive contains duplicate files")
                # Windows and macOS cannot hold two paths that differ only by
                # case, so a case-folded collision must fail here exactly as the
                # CI extractor already does.
                if relative.casefold() in seen:
                    raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive contains duplicate files")
                seen.add(relative)
                seen.add(relative.casefold())
                file_count += 1
                total_bytes += int(member.size)
                if file_count > SCOPED_ARCHIVE_MAX_FILES or total_bytes > SCOPED_ARCHIVE_MAX_BYTES:
                    raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive exceeds safety limits")
                source = package.extractfile(member)
                if source is None:
                    raise OracleCompatError("ORACLE_PACKAGE_ARCHIVE_INVALID", "Oracle package archive file is unreadable")
                archive_bytes = source.read()
                target = installed_files.get(relative)
                if target is None:
                    raise OracleCompatError(
                        "ORACLE_PACKAGE_TREE_MISMATCH",
                        "Installed Oracle package is missing a regular published file",
                        {"path": relative},
                    )
                if relative in contracts:
                    archive_hash = hashlib.sha256(archive_bytes).hexdigest()
                    contract = contracts[relative]
                    if archive_hash != contract["pristine"]:
                        raise OracleCompatError(
                            "ORACLE_PACKAGE_CONTRACT_MISMATCH",
                            "Published Oracle archive does not match the scoped patch contract",
                            {"path": relative, "actual": archive_hash, "expected": contract["pristine"]},
                        )
                    current_hash = sha256_file(target)
                    if current_hash not in {contract["pristine"], contract["patched"]}:
                        raise OracleCompatError(
                            "ORACLE_PACKAGE_TREE_MISMATCH",
                            "Installed Oracle patch target differs from the verified archive",
                            {"path": relative, "actual": current_hash},
                        )
                elif target.read_bytes() != archive_bytes:
                    raise OracleCompatError(
                        "ORACLE_PACKAGE_TREE_MISMATCH",
                        "Installed Oracle package differs from the verified published archive",
                        {"path": relative},
                    )
        if file_count == 0:
            raise OracleCompatError(
                "ORACLE_PACKAGE_ARCHIVE_INVALID",
                "Oracle package archive contains no regular files",
            )
        extra_files = sorted(set(installed_files) - seen)
        if extra_files:
            raise OracleCompatError(
                "ORACLE_PACKAGE_TREE_MISMATCH",
                "Installed Oracle package contains files absent from the verified archive",
                {"path": extra_files[0], "extra_count": len(extra_files)},
            )
        return root
    except (OSError, tarfile.TarError) as exc:
        raise OracleCompatError(
            "ORACLE_PACKAGE_ARCHIVE_INVALID",
            "Oracle package archive could not be verified",
        ) from exc


def package_version(package_root: Path) -> str:
    try:
        value = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OracleCompatError("ORACLE_PACKAGE_INVALID", "Oracle package.json is unreadable", {"root": str(package_root)}) from exc
    return str(value.get("version") or "").strip()


def _candidate_roots() -> list[Path]:
    override = str(os.environ.get("ORACLE_PACKAGE_ROOT") or "").strip()
    if override:
        return [Path(override).expanduser().resolve()]
    # Resolve the cache used by npx itself, including npmrc and environment
    # overrides. A Windows-shaped fallback can patch an unrelated installation
    # while the actual POSIX runner executes pristine, incompatible bytes.
    npm = shutil.which("npm")
    if not npm:
        raise OracleCompatError("ORACLE_NPM_CACHE_UNRESOLVED", "npm is required to locate the active Oracle cache")
    try:
        result = subprocess.run(
            [npm, "config", "get", "cache"], capture_output=True, text=True,
            timeout=15, check=False, **_git_kwargs(),
        )
        value = result.stdout.strip()
        cache = Path(value).expanduser()
        if result.returncode != 0 or not value or "\n" in value or not cache.is_absolute():
            raise ValueError("npm did not return one absolute cache path")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise OracleCompatError("ORACLE_NPM_CACHE_UNRESOLVED", "Cannot resolve the active npm cache") from exc
    roots = list((cache / "_npx").glob("*/node_modules/@steipete/oracle"))
    return sorted((path.resolve() for path in roots if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)


def resolve_package_root(version: str = SUPPORTED_VERSION) -> Path:
    candidates = _candidate_roots()
    matching = [path for path in candidates if package_version(path) == version]
    if not matching:
        raise OracleCompatError(
            "ORACLE_PACKAGE_NOT_FOUND",
            "The tested Oracle package is not installed in the npx cache",
            {"version": version, "candidates": [str(path) for path in candidates[:8]]},
        )
    return matching[0]


def resolve_package_roots(version: str = SUPPORTED_VERSION) -> list[Path]:
    candidates = _candidate_roots()
    matching = [path for path in candidates if package_version(path) == version]
    if not matching:
        raise OracleCompatError(
            "ORACLE_PACKAGE_NOT_FOUND",
            "The tested Oracle package is not installed in the npx cache",
            {"version": version, "candidates": [str(path) for path in candidates[:8]]},
        )
    return matching


def patch_root(version: str = SUPPORTED_VERSION) -> Path:
    return Path(__file__).resolve().parent / "oracle-compat" / version


def _git_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": startup}


def _apply_patch(package_root: Path, patch_path: Path, *, reverse: bool = False) -> None:
    isolated_env = os.environ.copy()
    # `git apply` also works outside a repository. Prevent a package root used
    # inside a test/worktree from being silently rebound to that parent repo.
    isolated_env["GIT_CEILING_DIRECTORIES"] = str(package_root.parent)
    patch_bytes = patch_path.read_bytes().replace(b"\r\n", b"\n")
    check = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "--ignore-space-change", *( ["-R"] if reverse else [] ), "--check", "-"],
        cwd=str(package_root),
        input=patch_bytes,
        capture_output=True,
        check=False,
        env=isolated_env,
        **_git_kwargs(),
    )
    if check.returncode != 0:
        raise OracleCompatError(
            "ORACLE_PATCH_CHECK_FAILED",
            "Oracle compatibility patch no longer matches the installed package",
            {"patch": str(patch_path), "stderr": (check.stderr or b"").decode("utf-8", errors="replace").strip()[-1200:]},
        )
    applied = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "--ignore-space-change", *( ["-R"] if reverse else [] ), "-"],
        cwd=str(package_root),
        input=patch_bytes,
        capture_output=True,
        check=False,
        env=isolated_env,
        **_git_kwargs(),
    )
    if applied.returncode != 0:
        raise OracleCompatError(
            "ORACLE_PATCH_APPLY_FAILED",
            "Oracle compatibility patch could not be applied",
            {"patch": str(patch_path), "stderr": (applied.stderr or b"").decode("utf-8", errors="replace").strip()[-1200:]},
        )


def _migrate_known_legacy_patch(
    package_root: Path,
    target: Path,
    relative: str,
    patch_path: Path,
    pristine_hash: str,
) -> None:
    """Restore one known former patch level to its verified pristine bytes."""
    with tempfile.TemporaryDirectory(prefix="oracle-compat-migrate-") as temporary:
        staged_root = Path(temporary)
        staged_target = staged_root / Path(relative)
        staged_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, staged_target)
        _apply_patch(staged_root, patch_path, reverse=True)
        if sha256_file(staged_target) != pristine_hash:
            raise OracleCompatError(
                "ORACLE_LEGACY_PATCH_RESTORE_INVALID",
                "Known legacy Oracle patch did not restore the exact pristine bytes",
                {"path": str(target), "patch": str(patch_path)},
            )
        shutil.copy2(staged_target, target)


def _validated_patch_target(root: Path, relative: str) -> Path:
    canonical_root = root.resolve(strict=True)
    target = canonical_root
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        target = target / part
        try:
            info = os.lstat(target)
            canonical_target = target.resolve(strict=True)
        except OSError as exc:
            raise OracleCompatError(
                "ORACLE_PACKAGE_TREE_MISMATCH",
                "Oracle patch target could not be inspected",
                {"path": relative},
            ) from exc
        if _is_link_or_reparse(info) or not canonical_target.is_relative_to(canonical_root):
            raise OracleCompatError(
                "ORACLE_PACKAGE_TREE_MISMATCH",
                "Oracle patch target crosses a link, junction, or package boundary",
                {"path": relative},
            )
        expected_kind = stat.S_ISREG if index == len(parts) - 1 else stat.S_ISDIR
        if not expected_kind(info.st_mode):
            raise OracleCompatError(
                "ORACLE_PACKAGE_TREE_MISMATCH",
                "Oracle patch target has an unexpected file type",
                {"path": relative},
            )
        if index == len(parts) - 1 and info.st_nlink != 1:
            raise OracleCompatError(
                "ORACLE_PACKAGE_TREE_MISMATCH",
                "Oracle patch target must not be hard-linked",
                {"path": relative},
            )
    return target


def _apply_oracle_compatibility(
    version: str,
    *,
    package_root: Path | None,
    backup_root: Path | None,
    contracts: dict[str, dict[str, Any]],
    patches: Path,
    package_roots: Sequence[Path] | None = None,
) -> dict[str, Any]:
    if package_roots is not None:
        roots = [root.expanduser().resolve(strict=True) for root in package_roots]
    else:
        roots = resolve_package_roots(version) if package_root is None else [package_root.expanduser().resolve(strict=True)]
    backup = backup_root or (Path.home() / ".codex" / "state" / "oracle-compat-backups" / version)
    changed: list[str] = []
    already: list[str] = []
    for root in roots:
        if package_version(root) != version:
            raise OracleCompatError("ORACLE_VERSION_MISMATCH", "Oracle package version does not match the resolved CLI version")
        for relative, contract in contracts.items():
            target = _validated_patch_target(root, relative)
            current = sha256_file(target)
            item = relative if len(roots) == 1 else f"{root}:{relative}"
            if current == contract["patched"]:
                already.append(item)
                continue
            backup_path = backup / Path(relative)
            if current in contract.get("legacy_patched", []):
                if not backup_path.exists() or sha256_file(backup_path) != contract["pristine"]:
                    legacy_patches = contract.get("legacy_patches")
                    legacy_patch = (
                        legacy_patches.get(current)
                        if isinstance(legacy_patches, dict)
                        else None
                    ) or contract.get("legacy_patch")
                    if not isinstance(legacy_patch, str) or not legacy_patch:
                        raise OracleCompatError(
                            "ORACLE_LEGACY_PATCH_BACKUP_INVALID",
                            "A legacy Oracle patch cannot be migrated without the exact pristine backup",
                            {"path": str(target), "backup": str(backup_path), "actual": current},
                        )
                    _migrate_known_legacy_patch(
                        root, target, relative, patches / legacy_patch, str(contract["pristine"])
                    )
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup_path)
                else:
                    shutil.copy2(backup_path, target)
                current = sha256_file(target)
            if current != contract["pristine"]:
                raise OracleCompatError(
                    "ORACLE_FILE_HASH_MISMATCH",
                    "Oracle compatibility refuses an unknown third-party file",
                    {"path": str(target), "actual": current, "expected": [contract["pristine"], contract["patched"]]},
                )
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if not backup_path.exists():
                shutil.copy2(target, backup_path)
            pristine_bytes = target.read_bytes()
            _apply_patch(root, patches / str(contract["patch"]))
            actual = sha256_file(target)
            if actual != contract["patched"]:
                restore_path: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        prefix=f".{target.name}.oracle-compat-rollback-",
                        suffix=".tmp",
                        dir=target.parent,
                        delete=False,
                    ) as stream:
                        stream.write(pristine_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                        restore_path = Path(stream.name)
                    shutil.copystat(target, restore_path, follow_symlinks=False)
                    os.replace(restore_path, target)
                    restore_path = None
                finally:
                    if restore_path is not None:
                        restore_path.unlink(missing_ok=True)
                raise OracleCompatError(
                    "ORACLE_PATCH_HASH_MISMATCH",
                    "Oracle compatibility patch output hash is unexpected",
                    {"path": str(target), "actual": actual, "expected": contract["patched"]},
                )
            changed.append(item)
    return {
        "ok": True,
        "version": version,
        "package_root": str(roots[0]),
        "package_roots": [str(root) for root in roots],
        "changed": changed,
        "already_patched": already,
    }


def ensure_oracle_compatibility(
    resolved_version: str,
    *,
    package_root: Path | None = None,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    """Apply only the default comprehensive-workflow Oracle contract."""
    version = resolved_version.strip().removeprefix("oracle ").strip()
    if version not in {SUPPORTED_VERSION, LKG_VERSION}:
        raise OracleCompatError(
            "ORACLE_VERSION_UNVALIDATED",
            "Oracle compatibility is validated only for current and rollback-LKG versions",
            {"resolved": resolved_version, "supported": [SUPPORTED_VERSION, LKG_VERSION]},
        )
    if version == SUPPORTED_VERSION:
        minimum, maximum = CURRENT_NODE_MAJOR_RANGE
        _verify_node_runtime(minimum, maximum, contract=f"current:{version}")
    contracts = PATCHES if version == SUPPORTED_VERSION else LKG_PATCHES
    return _apply_oracle_compatibility(
        version,
        package_root=package_root,
        backup_root=backup_root,
        contracts=contracts,
        patches=patch_root(version),
    )


def ensure_scoped_oracle_compatibility(
    resolved_version: str,
    *,
    profile: str,
    package_root: Path | None = None,
    package_archive: Path | None = None,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    """Apply an explicitly named, deployment-scoped Oracle contract."""
    normalized_profile = profile.strip() if isinstance(profile, str) else ""
    profile_versions = SCOPED_PATCHES.get(normalized_profile)
    if profile_versions is None:
        raise OracleCompatError(
            "ORACLE_COMPAT_PROFILE_UNVALIDATED",
            "Oracle compatibility profile is not validated",
            {"profile": profile, "supported_profiles": sorted(SCOPED_PATCHES)},
        )
    version = resolved_version.strip().removeprefix("oracle ").strip()
    contracts = profile_versions.get(version)
    if contracts is None:
        raise OracleCompatError(
            "ORACLE_VERSION_UNVALIDATED",
            "Oracle compatibility is not validated for this scoped profile",
            {"resolved": resolved_version, "profile": normalized_profile, "supported": sorted(profile_versions)},
        )
    expected_integrity = SCOPED_PACKAGE_INTEGRITIES.get(normalized_profile, {}).get(version)
    if not expected_integrity or package_archive is None:
        raise OracleCompatError(
            "ORACLE_PACKAGE_ARCHIVE_REQUIRED",
            "Scoped Oracle compatibility requires the exact published package archive",
            {"profile": normalized_profile, "version": version},
        )
    if package_root is None:
        raise OracleCompatError(
            "ORACLE_PACKAGE_ROOT_REQUIRED",
            "Scoped Oracle compatibility requires the exact installed package root",
            {"profile": normalized_profile, "version": version},
        )
    _verify_scoped_node_runtime(normalized_profile)
    roots = [package_root.expanduser()]
    verified_roots: list[Path] = []
    for root in roots:
        verified_roots.append(
            _verify_scoped_package_archive(
                root,
                package_archive,
                expected_integrity=expected_integrity,
                contracts=contracts,
            )
        )
    result = _apply_oracle_compatibility(
        version,
        package_root=None,
        backup_root=backup_root,
        contracts=contracts,
        patches=patch_root(version),
        package_roots=verified_roots,
    )
    for root in verified_roots:
        _verify_scoped_package_archive(
            root,
            package_archive,
            expected_integrity=expected_integrity,
            contracts=contracts,
        )
    return {**result, "profile": normalized_profile, "package_integrity": expected_integrity}


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Apply an exact, hash-gated Oracle ChatGPT compatibility patch.")
    parser.add_argument("--resolved-version")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--package-archive", type=Path)
    parser.add_argument("--profile", choices=sorted(SCOPED_PATCHES))
    args = parser.parse_args(argv)
    try:
        if args.profile:
            if not args.resolved_version:
                raise OracleCompatError(
                    "ORACLE_VERSION_REQUIRED",
                    "Scoped Oracle compatibility requires an explicit resolved version",
                    {"profile": args.profile},
                )
            result = ensure_scoped_oracle_compatibility(
                args.resolved_version,
                profile=args.profile,
                package_root=args.package_root,
                package_archive=args.package_archive,
            )
        else:
            result = ensure_oracle_compatibility(
                args.resolved_version or f"oracle {SUPPORTED_VERSION}",
                package_root=args.package_root,
            )
    except OracleCompatError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": str(exc), "evidence": exc.evidence}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
