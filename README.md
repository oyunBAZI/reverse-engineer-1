# reverse-engineer-1 — `com.fun.lastwar.gp` source recovery

Reverse engineering of an Android APK to recover the game's source code, logic
and assets.

**Target:** `com.fun.lastwar.gp` (Last War: Survival Game — FunPlus, Unity Mono build)
**Input:** `input/app.apk` (813 MB, 1,947 entries, 9 DEX files, 53 native libs)
**Authorization:** target owned by the requester / CTF / authorized.

---

## Read this first

The game's **source code is recovered and is in this repo**: 18,300 Lua modules
of game logic (2,338,933 lines; 18,243 of them non-empty, and **all 18,300
decompile** — 0 failures), 1,275 game config tables, 3,624 C# files, 17,325 Java
files and 7,564 extracted Unity data files. The 117 original managed assemblies
and the art/audio are recovered in the working tree but not tracked — see *What
is and is not in version control*.

A **runnable game** is a different thing, and it is not what an APK decompile
yields. Building it back would require reimplementing the Unity engine
(`libunity.so`, 23 MB of compiled native code), the Lua VM (`libxlua.so`),
several proprietary SDK plugins, and the ~25,000 AssetBundles that live on the
studio's CDN rather than in the APK — plus a backend, since the client
authenticates against `lastwar-serverlist-*`. `unity-project/` is laid out as a
real Unity project with the recovered content in its original directories, but
it will not compile into a shippable game. That boundary is a property of the
input, not a gap in the extraction.

---

## What is in this repo

```
input/app.apk                     the original APK (untouched)

tools/re-env.sh                   pins the JDK / jadx / apktool locations
tools/decompile.sh                APK -> Java, resources, assets, Lua, tables
tools/decompile-csharp.sh         .mdl -> .dll -> C# source
tools/extract-lua.py              unpacks the LWLF script container
tools/unluac-batch/               batch Lua bytecode decompiler driver
                                  (also does the header fix, in memory)
tools/unluac-batch/patch/         the 2 unluac sources patched in-repo
tools/unluac-batch/recovered/     5 hand-reconstructed modules + patches
tools/check-lua-syntax.py         compiles every recovered .lua (FAIL/WARN gate)
tools/check-asset-inventory.py    asserts the extracted assets match the inventory
tools/asset-completeness-report.py  classifies every asset path: in-APK vs. CDN-only
tools/extract-unity-assets.py     bin/Data Unity files -> textures/scenes/shaders
tools/extract-game-assets.py      packed AssetBundle fragment -> art and audio
tools/peprobe/                    .NET helper that validates recovered PE images

source-app/src/                   17,325 decompiled Java files (jadx)      [tracked]
source-app/csharp/                3,624 decompiled C# files (ILSpy)       [tracked]
source-app/lua/src/               18,300 decompiled Lua modules           [tracked]
                                  (2,338,933 lines, 0 decompile failures)
source-app/data-tables-lua/       1,275 config tables decompiled to Lua  [tracked]
source-app/unity-assets/          7,564 files: boot scene, shaders,        [tracked]
                                  textures, MonoScript table, settings

source-app/lua/luac/              18,300 Lua bytecode chunks, as shipped   [local]
source-app/data-tables/           1,275 config tables, bytecode, shipped    [local]
source-app/game-assets/           art/audio pulled from the bundle         [local]
                                  fragment + inventory.jsonl

decompiled/apktool/               decoded AndroidManifest.xml + res/ (no smali)
decompiled/raw/classes*.dex       the 9 DEX files, unmodified
decompiled/raw/lib/<abi>/         53 native .so files
decompiled/unity/assemblies/      117 recovered .NET assemblies (.dll)
decompiled/unity/assets/          the full Unity asset payload as shipped

unity-project/                    the game reassembled as a Unity project,
                                  in its original Assets/ layout (symlinks)
asset_completeness_report.{md,json}  the asset boundary, per path (generated
                                  by tools/asset-completeness-report.py)
.github/workflows/ci.yml          validates the pipeline on every push/PR
tools/ci-checks.sh                the checks CI runs (also runnable locally)
```

### What is and is not in version control

**The recovered source is committed.** A clone of this repository contains the
game's Java, C#, Lua, config tables and Unity assets — 48,092 files — without
needing the APK or running anything.

| Tracked | Files |
|---|---|
| `source-app/src/` — decompiled Java (jadx) | 17,325 |
| `source-app/lua/` — decompiled Lua modules | 18,300 |
| `source-app/data-tables-lua/` — config tables decompiled to Lua | 1,277 |
| `source-app/csharp/` — decompiled C# (ILSpy) | 3,626 |
| `source-app/unity-assets/` — boot scene, shaders, MonoScript table | 7,564 |

What stays out is the pipeline's input, its pass-through intermediates, and one
container that cannot be pushed at all:

| Not tracked | Why |
|---|---|
| `input/app.apk` (775 MB) and two 498 MB copies of `BundleFragment0.bytes` | exceed GitHub's 100 MB per-file hard limit |
| `source-app/**/*.luac`, `source-app/data-tables/` | bytecode the decompiler consumes; the recovered `.lua` beside it is the deliverable |
| `source-app/game-assets/` | 307 MB across 11,795 already-compressed PNG/WAV/TextAsset files. Regenerated deterministically by the `bundles` step, and far too heavy to add to every clone |
| `decompiled/` | DEX, `.so` and Unity payloads exactly as shipped; the decoded forms are tracked |
| per-run diagnostics, build output, `__pycache__` | regenerated on every run |

Everything in that second table is **regenerated deterministically** by the
pipeline below, and the step guards fail loudly rather than silently producing
nothing. Two details make the tracked payload trustworthy:

- `.gitattributes` marks generated content `-text`, so no checkout or clone can
  rewrite line endings or re-encode a file the pipeline produced. The
  hand-written toolchain is explicitly LF (`.sh`, `.py`, `.md`, `.yml`), and the
  vendored `unluac.jar` is pinned `binary` — a path glob like `/tools/**` would
  have marked that zip as text and stripped its CR bytes on the next `git add`.
- Ignore rules that exclude a path are anchored (`/input/`, `/decompiled/`). An
  unanchored `input/` silently swallowed 269 recovered files under
  `androidx/compose/**/input/` before this was caught.
- `tools/ci-checks.sh` now guards all of it: no tracked file over 100 MB, no
  tracked file matching an ignore rule, no payload file reachable by a text
  rule, every payload blob matching its bytes on disk, every recovered `.lua`
  compiling, the extracted assets matching their inventory, and README's file
  counts matching the tracked tree.

`unity-project/Assets/*` are symlinks into the payload. Three of the five
(`LuaScripts`, `DataTable`, `CSharp`) now resolve straight from a fresh clone;
`HotUpdateDll` points at `decompiled/unity/assemblies` and `Art` at
`source-app/game-assets`, both untracked, so `bash unity-project/sync-links.sh`
reports those two as missing until the pipeline has produced them.

---

## The app

| | |
|---|---|
| Package | `com.fun.lastwar.gp` |
| compileSdkVersion | 35 (Android 15) |
| Platform | Unity 2019.4.41f1, **Mono** scripting backend |
| Managed code | `assets/Assemblies/*.mdl`, 117 assemblies |
| Framework | XLua + Lua hot-update layer (`libxlua.so`, `assets/lwScripts/`) |
| Networking | SmartFox2X realtime server + `BestHTTP` |
| Notable SDKs | AppsFlyer, Thinking Analytics, Shumei, GME/Tencent voice, Zendesk, Google Ads, Vungle |

The declared package set also includes `com.fun.lastwar.debug`, `com.lastwar.ios`
and `com.lastwar.pc` (see `ConstURLConfig.cs`), so this is the Google Play
variant of a multi-platform client.

---

## Findings

### 1. Managed assemblies are obfuscated, not encrypted

The 117 game assemblies ship as `assets/Assemblies/*.mdl` instead of `.dll`. The
protection is a **single-byte XOR applied to a short prefix of each file's DOS
header**, and two details make it weaker than it looks:

- the key is **different per file** (36 distinct keys observed, `0x07`–`0x30`)
- the obfuscated prefix is **a different length per file** (`0x0c`–`0x20` bytes)

Everything past the prefix is verbatim, including the CLI header, the `BSJB`
metadata root and all IL. So `MZ` in byte 0 yields the key immediately, and the
prefix length is found by trying candidates and validating against a real PE/CLR
layout. `tools/decompile-csharp.sh` does this and recovers **117 of 117**
assemblies with no failures.

`libil2cpp.so` ships in the APK but is not the backend in use — the `libmono-*`
set and the shipped assemblies confirm Mono. That is also why there is no
`global-metadata.dat` or `ScriptingAssemblies.json`: those exist only for
IL2CPP, so their absence is expected, not a failure.

### 2. The Lua payload is the bulk of the game's logic

`assets/lwScripts/LWScripts.data` (99 MB) is a container the game calls `LWLF`.
Its format came from the game's own loader — the decompiled
`LWLuaFile._Load` in `source-app/csharp/Assembly-CSharp/LWLuaFile.cs`:

```
"LWLF"                 magic (LWLuaFileUtil.s_Magic)
int32 fileVersion     1 = original, 2 = SuperEncrypt-ed payloads
int32 version         LWLua version
int32 entryCount
entryCount x { string name; int32 length; byte data[length] }
```

Parsing it yields **18,300 Lua modules — 102,804,160 of 103,989,065 bytes
accounted for, and the entries consume the file exactly with zero trailing
bytes.** That exact fit is the validation: a wrong entry count cannot land on
EOF. Every payload begins `\x1bLua`, so all 18,300 are Lua bytecode. `759` in
the sidecar manifest is the Lua version, not the file count.

### 3. The studio ships a modified `luac`, and the fix is one byte plus one

Every chunk carries a non-standard header:

```
studio: sig(4) ver(1) fmt=0x01(1) DATA(6) 04 04 08 08   LUAC_INT LUAC_NUM ...
upstream Lua 5.3 + unluac:
        sig(4) ver(1) fmt=0x00(1) DATA(6) 04 04 04 08 08 LUAC_INT LUAC_NUM ...
```

Two differences: the format byte is `0x01`, and there are four size bytes
(`int`, `Instruction`, `Integer`, `Number`) where unluac's `LHeaderType53`
expects five (`int`, `size_t`, `Instruction`, `Integer`, `Number`). Rewriting
the format byte and inserting one `0x04` realigns them exactly. Only the header
changes — every byte of prototype, code, constants and debug info is passed
through untouched.

With that done, stock unluac recovers **18,240 of 18,300** modules, and the
remaining 60 are not all the same thing:

- **57** are genuinely empty — their disassembly is a lone `return` with no
  constants or code, so the original `.lua` held nothing but comments
- **3** hit a `NullPointerException`, and nothing short of fixing unluac gets
  them back

### The 3 failures are a one-line bug in unluac

`ControlFlowHandler.is_break_jmp` dereferences a prototype that can be null:

```java
if (target.getEnd() == breakable.getEnd()) { ... }
```

Six sibling call sites in the same file guard the same dereference; this one
does not, so a `goto` aimed at something that is not a block kills the
decompiler. Both offending sources are vendored in `tools/unluac-batch/patch/`
with the guard added, and `tools/unluac-batch/build-patch.sh` compiles them
against the jar so they override only the classes they replace.

The result is **18,243 recovered, 57 empty, 0 failed** — all 18,300 accounted
for, with no bytecode listings standing in for source. The proof that this is
the patch and nothing else: jar-first on the classpath gives 18,240 / 3, and a
clean patched run differs from the committed tree in exactly the 5 files the
recovery overlay touches, leaving 18,294 files byte-identical.

Two ways this silently fails, both now guarded by `tools/ci-checks.sh`: put
`unluac.jar` ahead of `classes/` on the classpath and the JVM loads upstream's
class, so the patch compiles, runs, and recovers nothing.

`tools/check-lua-syntax.py` then compiles every recovered file: **18,300 modules
(18,296 ok, 4 host-version warnings, 0 failures) plus all 1,275 config tables.**
It separates real parse errors from artifacts of the host interpreter, which is
Lua 5.5 here rather than the game's 5.3 — the 4 warnings are assignments to a
`for` loop's control variable, which 5.4+ rejects and 5.3 accepts.

The same applies to `assets/table/*.data`, which is a plain ZIP of 1,275
bytecode modules using the identical header. **All 1,275 decompile.** The two
largest (`lw_monster_opt`, 8 MB; `lw_world_monster`) need `-Xmx3000m`; the
driver takes `LUA_MEM` for exactly this.

### 5 modules unluac gets structurally wrong

`Common/mobdebug.lua` and friends aside, unluac can also exit **0** on output
its own parser rejects: five modules contain a `goto` whose label lands inside
a block, which is not valid Lua. `tools/unluac-batch/recovered/` holds them as
patches reconstructed by reading the bytecode listings, applied by
`apply-recovered.sh` after every Lua stage. It is idempotent, and a patch that
applies in neither direction is a hard error rather than a silent skip.

### 4. The original `Assets/` tree is embedded in the bytecode

Every chunk stores the absolute source path it was compiled from:

```
/Users/mac/Git-Android-release/aps_client/Tools/../Assets/Main/LuaScripts/...
```

All 18,300 parse cleanly. So the project layout in `unity-project/` — the
`Assets/Main/LuaScripts/<category>/` split, `Assets/Main/HotUpdateDll/`,
`Assets/DataTable/`, the `Assets/Main/...` art paths — is the game's real
directory structure read back out of the binaries, not a reconstruction guess.

### 5. Assets ship as one packed fragment, keyed by hash

`assets/AssetBundles/BundleFragment0.bytes` is 522 MB of **concatenated
`UnityFS` bundles**. `gameres` is a 16 MB text manifest: **11,155 directories,
95,602 asset paths, 32,564 bundles, 46 groups**.

Three things bite here:

- The bundles carry the literal version strings `5.x.x` and `0.0.0` — the studio
  stripped the engine version, so UnityPy refuses them until
  `FALLBACK_UNITY_VERSION` is set to `2019.4.41f1` (read from the APK's own
  build settings).
- The shipped `BundleOffsetTable.bytes` is **wrong**: its name/offset pairing is
  shifted by one record and one of its offsets lands in the middle of another
  bundle. It is kept only as a cross-check. The fragment is instead walked from
  byte 0, because every `UnityFS` header records its own total size — which
  makes the layout self-describing and lets the walk *prove* it: 7,868 bundles
  consuming all 522,139,911 bytes with zero left over, and the magic re-checked
  at every boundary so a misparse cannot yield a plausible-looking list.
- Each bundle's real name is therefore read from its own `AssetBundle` object,
  and asset names are resolved against the `gameres` path index — recovered
  files land under true in-game paths such as
  `Assets/Main/ActivityRes/2025EasterMod/Sprites/ActivityIcons/...png`.

### What the sweep recovers

All 7,868 bundles, 0 failures, 977,059 objects in ~148 s (≈53 bundles/s):

| | |
|---|---|
| Sprite | 11,730 |
| Texture2D | 9,377 |
| TextAsset | 705 |
| AudioClip | 613 |
| files written | 11,795 (307 MB) |

**9,229 of the 9,377 textures ship with an empty pixel stream** — their pixels
live in one of the ~25,000 bundles the studio serves from its CDN. These are
recorded in `inventory.jsonl` by name and path_id, not written as files, and
that is a correctness decision rather than tidiness: asked to decode such a
texture, UnityPy does not fail, it returns a full-resolution image spanning the
whole 0-255 range, so the previous revision wrote **5,281 PNGs of noise that
sized like real art (mean ~130 KB, 443 MB in total)** and looked fine in a file
browser. `tools/extract-game-assets.py` refuses to write them and says why.

**How much of the game's art this is** is measured rather than estimated:
`asset_completeness_report.{md,json}` (generated and CI-checked by
`tools/asset-completeness-report.py`) classifies all 95,602 asset paths the
`gameres` manifest describes. 35,699 (37.3%) are reachable from the APK and
59,903 (62.7%) live only in the ~24,696 bundles the studio serves from its
CDN; every path is listed with its class in the JSON.

**The audio is in the APK**, which the object census initially got wrong. All
613 `AudioClip`s carry a zero-length `m_AudioData`, which reads as "shipped on
the CDN" — but UnityPy's `samples` returns complete WAV streams regardless, and
indexing that dict as a list raised `KeyError: 0` and reported every clip as a
decode failure. Fixing the handling recovers **466 files, 24 minutes, 118 MB**
of voice-over, all valid 22 kHz mono/stereo WAV.

Two sidecars make the sweep auditable and restartable: `.state.tsv`
(offset → status, name, size) and `inventory.jsonl` (one row per object, sorted
by fragment offset, `file` paths relative to the output root). Both are rebuilt
in memory and rewritten atomically, so a re-extract replaces its rows rather
than appending to them. `--force` means "redo this bundle's extraction", and a
run the time budget cuts short marks what it did not reach as `todo` rather than
leaving stale rows behind claiming to be current.

Only the 7,868 bundles inside the APK are extractable; the other ~25,000 are
fetched from the studio's CDN on first run.

### 6. Hot-update path is fully visible

`ConstURLConfig.cs` hardcodes the infrastructure:

```csharp
public static readonly string[] cdns = {
  "https://lastwar-cdn.akamaized.net/hotupdate/",
  "https://lastwar-cdn.lastwarapp.net/hotupdate/",
  "https://cdn.lastwar.com/hotupdate/",
  "https://lastwar.asia-cdn.com/hotupdate/"
};
public const string debugDownloadURL_ = "http://lw-local-s188.gamespark.net/hotupdate/";
```

plus server-list hosts per region (`lastwar-serverlist-us-aws-ali`, `-gcp-ali`,
EA variants, a pressure-test host, and localhost GM hosts). The version check
endpoint is `/gameservice/getlsu3dversion.php`.

This is an older build: it still carries the `LuaUpdater` / `LWLuaFile` path,
and the C# tree has per-season systems (`WinterStorm`, `Werewolf`, `NineNation`,
`DarkSeason`, `LondonSeason`, `FlowerTrain`). Expect the live app to have moved on.

### 7. C# type tree is fully intact

`Assembly-CSharp.dll` decompiles to 5,749 types / 53,628 methods, covering the
whole game: city building and troop state machines (`WorldTroop*`, `CityTroop*`),
season/boss systems, the cross-server FSM (`CrossServer*`), asset/manifest
download, and the SDK bridge layer.

---

## Reproducing

Toolchain (JDK 17, jadx 1.5.6, apktool 2.9.3, .NET 8 + ilspycmd, UnityPy) lives
in `/opt/re-tools` where applicable; paths are pinned in `tools/re-env.sh`.

```bash
bash tools/decompile.sh              # Java + resources + native libs + assets
                                    # + Lua + config tables + built-in Unity assets
bash tools/decompile-csharp.sh       # recover .mdl -> decompile to C#
```

Steps are independent and re-runnable, and select work with environment
variables:

```bash
STEPS="apktool raw unity" bash tools/decompile.sh

# chunk the long jadx step on a small box
DEXES="classes4.dex" bash tools/decompile.sh

# heap for the Lua decompiler; the biggest data table needs ~3g
LUA_MEM=3000m STEPS=tables bash tools/decompile.sh

# AssetBundle extraction is resumable; budget is seconds per invocation
BUNDLE_BUDGET=150 STEPS=bundles bash tools/decompile.sh
BUNDLE_BUDGET=0    STEPS=bundles bash tools/decompile.sh   # all 7,868, ~90 min
```

`tools/peprobe` is the validation helper used to work out the `.mdl` format; it
reports PE headers, the CLI header and metadata counts per assembly:

```bash
dotnet run --project tools/peprobe -- decompiled/unity/assemblies/Assembly-CSharp.dll
# magic=PE32 machine=I386 sections=3 corHeader=present
# version=v4.0.30319 types=5749 methods=53628
```

Scripts must be invoked with `bash`, not `sh` — `decompile.sh` uses `pipefail`,
which dash does not support.

---

## Caveats

- The decompiled trees are not meant to compile as-is. They are decompilation
  output: identifiers the compiler already discarded are gone, and library code
  (AndroidX, Kotlin, OkHttp, ad SDKs) is included because it is what ships.
  `source-app/csharp/Assembly-CSharp/Assembly-CSharp.csproj` is the real signal
  for the game's own types.
- The Lua bytecode tables decompile into register-style output
  (`L0_1 = {...}`) — semantically complete and machine-generated, but not
  hand-written-looking source.
- Java output is jadx 1.5.6 with `--deobf`; obfuscated SDK classes keep
  generated names (`C0972R`, `p000j$`), which is expected for library layers.
- Native libraries are extracted, not analyzed. `libunity.so`, `libil2cpp.so`
  (unused), `libxlua.so`, `libgmesdk.so` and `libanogs.so` are the ones that
  would matter for deeper work.
- `source-app/game-assets/` is not tracked, but it is **complete**, not partial:
  all 7,868 bundles in the APK, 0 failures. It is excluded because it is 307 MB
  of already-compressed PNG and WAV that the `bundles` step reproduces from the
  APK in ~148 s, not because it is unfinished.
- 3 objects in the sweep still fail to decode (2 textures whose format UnityPy
  mishandles, 1 sprite with a null texture pointer). They are listed in
  `inventory.jsonl` with the error rather than dropped silently.
- The Lua syntax gate compiles with Lua 5.5 (lupa), while the game's compiler is
  5.3-era. It agrees on every real parse error but rejects assignments to a
  `for` loop's control variable, which 5.3 accepts — 4 such warnings, reported as
  WARN rather than passed over. A real `lua5.3` binary would settle it.
