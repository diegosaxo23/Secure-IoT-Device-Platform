#pragma once

/*
 * The distributable source tree contains no installation-specific CA.
 * scripts/factory_program_esp32.py writes the active public Root CA into the
 * local, git-ignored .factory-build-cache directory. Keeping that generated
 * file stable lets PlatformIO reuse a previous build when nothing changed.
 */
#if defined(__has_include)
#  if __has_include("../.factory-build-cache/bootstrap_ca.generated.h")
#    include "../.factory-build-cache/bootstrap_ca.generated.h"
#  else
static const char IOT_BOOTSTRAP_ROOT_CA[] = "";
#  endif
#else
static const char IOT_BOOTSTRAP_ROOT_CA[] = "";
#endif
