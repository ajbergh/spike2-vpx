.RECIPEPREFIX := >

BUILD  ?= build
ROOT   ?= $(HOME)/spike2root
CROSS  ?= arm-linux-gnueabihf-
PYTHON ?= python3
DESTDIR ?=
DEJAVU ?= /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
TABLES ?= $(ROOT)/dump/tables/turtles_pro/switch_list.txt

# ---- Stern: the ARM cross build against the card rootfs ----
CC        = $(CROSS)gcc
BINNAME   = codeselect
PREFIX   ?= /usr/local/codeselect
# QEMU reaches the test scripts through the ENVIRONMENT, never their argv:
# the rig's teardown (killgame.sh, watch.sh) does pkill -9 -f 'arm-binfmt|qemu-arm',
# and `bash test/headless.sh qemu-arm-static ...` matched it - a run torn down
# beside `make check` killed the tests. Only the short-lived qemu child's own
# command line matches now.
QEMU     ?= qemu-arm-static
export QEMU
GCCINC   := $(shell $(CC) -print-file-name=include)
CRTBEGIN := $(shell $(CC) -print-file-name=crtbegin.o)
CRTEND   := $(shell $(CC) -print-file-name=crtend.o)
# -std=gnu17: the dialect is a decision, not whatever the distro's gcc defaults
# to this year.  gcc 11 (22.04) and 13 (24.04) already default to it; gcc 15
# defaults to gnu23, where bool/true/false are keywords - the change that broke
# criu here already, and which would otherwise arrive in these sources with the
# next LTS and no warning.
CFLAGS    = -std=gnu17 -O2 -Wall -Werror=implicit-function-declaration \
            -U_TIME_BITS -U_FILE_OFFSET_BITS -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 \
            -fno-stack-protector -fno-pie -no-pie \
            -nostdinc -isystem $(GCCINC) -isystem $(ROOT)/usr/include -I$(BUILD) $(EXTRA_CFLAGS)
LDPRE     = -nostdlib -nostartfiles $(ROOT)/usr/lib/crt1.o $(ROOT)/usr/lib/crti.o $(CRTBEGIN)
LDPOST    = $(CRTEND) $(ROOT)/usr/lib/crtn.o -L$(ROOT)/lib -L$(ROOT)/usr/lib \
            -Wl,-rpath-link,$(ROOT)/lib -Wl,-rpath-link,$(ROOT)/usr/lib \
            -Wl,--dynamic-linker=/lib/ld-linux-armhf.so.3
# libc_nonshared.a: __libc_csu_init/fini for the rootfs crt1.o + the stat wrappers
# -l:libgcc_s.so.1: the rig's bridge shims leave __aeabi_uldivmod undefined
# -l:libpthread.so.0: the input scan and the animation cache are threads (2.8)
LIBC      = -l:libpthread.so.0 -l:libc.so.6 $(ROOT)/usr/lib/libc_nonshared.a -l:libm.so.6 -l:libgcc_s.so.1 -lgcc
GLLIBS    = -l:libEGL.so.1 -l:libGLESv2.so.2
# the rootfs alsa-lib 1.0.28 (GLIBC_2.4 only; its libdl/libpthread/librt are
# in the rootfs). Hand prototypes in audio_alsa.c: no headers on the box.
AUDLIBS   = -l:libasound.so.2
SRCS = codeselect.c conf.c colour.c settings.c testpics.c video.c theme.c gfx.c egl_stern.c input.c input_hw.c input_padsw.c input_pipe.c log.c art.c audio.c audio_fifo.c audio_alsa.c codec.c nvm.c scores.c
HDRS = conf.h colour.h settings.h testpics.h video.h theme.h gfx.h egl_stern.h input.h log.h art.h audio.h codec.h nvm.h scores.h $(BUILD)/theme_table.h

# THE PAST-32 BINARY (item 105).  The animation tick used to mark the cards
# that moved in an `unsigned` bitmask (`1u << i`), which is undefined from
# card 32 up and fails silently.  It is a flag array now, and this second
# build of the SAME sources at a raised cap is what proves it: a menu is
# CONF_MAX_CARDS wide (16) and no conf can talk the shipped binary past that,
# so without this build the fix would stay untested.  Item 106 raised the
# IMAGE cap to 64 and left CARDS at 16 - a group of forty variants is one
# card - so the override moved to the cap that actually sizes the tick.
# Built by `make check`, never installed, never written to a card.
CAP_N    ?= 40
CAPBUILD  = $(BUILD)-cap$(CAP_N)
CAPBIN    = $(CAPBUILD)/$(BINNAME)

OBJS = $(SRCS:%.c=$(BUILD)/%.o)
BIN  = $(BUILD)/$(BINNAME)


# PAD-1309: padmenu.so, preloaded into the game on a multi-boot card - BACK held
# on the coin door restarts the machine into the menu.  A shared object, built
# the way the rig's hwshim.so is (build.sh): -nostdlib against the rootfs's own
# libdl/libc, so it asks nothing newer than the card's glibc 2.21.
PADMENU = $(BUILD)/padmenu.so
all: $(BIN) $(PADMENU)

$(PADMENU): padmenu.c | $(BUILD)
> $(CC) -std=gnu17 -O2 -Wall -Werror=implicit-function-declaration     -U_TIME_BITS -U_FILE_OFFSET_BITS -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0     -fno-stack-protector -nostdinc -isystem $(GCCINC) -isystem $(ROOT)/usr/include     -fPIC -shared -nostdlib -Wl,-soname,padmenu.so padmenu.c     -L$(ROOT)/lib -L$(ROOT)/usr/lib -l:libdl.so.2 -l:libc.so.6 -lgcc -o $@ || { rm -f $@; exit 1; }
> @echo "built $@"

$(BUILD):
> mkdir -p $(BUILD)

# The colour themes: themes.json is the one definition (mkmulticard.py and the
# app read the same file), turned into a header in the build dir - generated,
# never committed, and checked by the generator (a bad JSON fails the build).
$(BUILD)/theme_table.h: themes.json gen_themes.py | $(BUILD)
> $(PYTHON) gen_themes.py themes.json $@

$(BUILD)/%.o: %.c $(HDRS) | $(BUILD)
> $(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/gfx.o: third_party/stb_truetype.h
$(BUILD)/art.o: third_party/stb_image.h


# a failed link deletes its output: never leave a stale binary behind
$(BIN): $(OBJS)
> $(CC) $(CFLAGS) $(LDPRE) $(OBJS) $(LDPOST) $(GLLIBS) $(AUDLIBS) $(LIBC) -o $@ || { rm -f $@; exit 1; }
> @echo "built $@"

# a recursive build of the same sources: only BUILD and the cap differ
capbin:
> $(MAKE) BUILD="$(CAPBUILD)" EXTRA_CFLAGS=-DCONF_MAX_CARDS=$(CAP_N) "$(CAPBIN)"


check: $(BIN) capbin $(PADMENU)
> bash test/check_elf.sh "$(CROSS)" "$(BIN)"
> bash test/padmenu_test.sh "$(ROOT)" "$(PADMENU)"
> CAPBIN="$(CAPBIN)" CAP_N=$(CAP_N) bash test/headless.sh "$(ROOT)" "$(BIN)" "$(BUILD)/t" "$(DEJAVU)"
> python3 test/padsw_test.py "$(ROOT)" "$(BIN)" "$(BUILD)/t" "$(TABLES)" "$(DEJAVU)"
> python3 test/settings_test.py "$(ROOT)" "$(BIN)" "$(BUILD)/t" "$(DEJAVU)"
> bash -n select.sh && bash test/select_sh_test.sh
> @echo "check: OK"

check-hw: $(BIN)
> python3 test/fakebus_test.py "$(ROOT)" "$(BIN)" "$(BUILD)/t" "$(DEJAVU)"
> @echo "check-hw: OK"

install: $(BIN) $(PADMENU)
> install -d "$(DESTDIR)$(PREFIX)"
> install -m 755 "$(BIN)" "$(DESTDIR)$(PREFIX)/codeselect"
> install -m 755 "$(PADMENU)" "$(DESTDIR)$(PREFIX)/padmenu.so"
> install -m 755 select.sh "$(DESTDIR)$(PREFIX)/select.sh"
> install -m 755 materialize.py "$(DESTDIR)$(PREFIX)/materialize.py"
> install -m 644 images.conf.example "$(DESTDIR)$(PREFIX)/images.conf.example"
> if [ -f "$(DEJAVU)" ]; then install -m 644 "$(DEJAVU)" "$(DESTDIR)$(PREFIX)/font.ttf"; else echo "no $(DEJAVU): the card's VeraMono.ttf will be used"; fi
> if [ -d media ]; then install -d "$(DESTDIR)$(PREFIX)/media"; for f in media/*; do if [ -f "$$f" ]; then install -m 644 "$$f" "$(DESTDIR)$(PREFIX)/media/"; fi; done; fi

clean:
> rm -rf "$(BUILD)" "$(CAPBUILD)"

.PHONY: all capbin check check-hw install clean
