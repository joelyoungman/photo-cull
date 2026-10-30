# Photo Cull

Pick the best photos from one shoot, quickly. It runs on your Mac with nothing sent online and no subscription.

Choose a shoot's folder and Photo Cull looks at every photo in it. It groups bursts and look-alike shots and suggests
the best of each group. It flags photos that look blurry, have closed eyes, or are too dark or bright. You stay in
charge: you go through with the keyboard, pick and star what you want, and copy the picks to a **Selects** folder.

**Your originals are never changed, moved or deleted.** Photo Cull only reads them. The one thing it writes inside
your shoot folder is the Selects folder, and only when you ask. It never overwrites a file there.

## Install (Mac)

1. Download **PhotoCull-mac.zip** from the [latest release](../../releases/latest) and double-click it to unzip.
2. In the **Photo Cull** folder, double-click **Install Photo Cull.command**.
3. The first time, macOS says it can't check the file, because it isn't from the App Store. Click **Done**, then:
   - open **System Settings → Privacy & Security**,
   - scroll down to *"Install Photo Cull.command" was blocked* and click **Open Anyway**,
   - confirm with Touch ID or your password.

   You only do this once. If you prefer, open Terminal, type `bash ` (with a space), drag the installer onto the
   window and press Return.
4. A Terminal window shows progress. The first install downloads about 250 MB and takes a few minutes. When it says
   **Done!**, Photo Cull opens in your browser.

After that, open **Photo Cull** from Applications, Launchpad or Spotlight. It needs the internet only while
installing.

To update, download the new zip and run the installer again. Your ratings are kept. To remove Photo Cull,
double-click **Uninstall Photo Cull.command**. Your photos and Selects folders stay.

## Use

1. Click **Choose shoot folder…** and pick the job's folder. Sub-folders (one per memory card, say) are included.
   Photos appear as they're analysed: roughly a few minutes for a few hundred photos.
2. Go through the photos with the keyboard:

| Key | Does |
|---|---|
| ← → | previous / next photo |
| ↑ ↓ | previous / next group, landing on the suggested best |
| **P** / **X** / **U** | pick / reject / undo |
| **1–5**, **0** | star rating, clear stars |
| **C** | compare the whole group side by side (← → to choose, P/X to mark) |
| **G** | grid of everything |
| **Z** or click | zoom to 100%; move the mouse to look around |
| **F** | show the faces it found (red = eyes closed) |
| **B** | switch between *All photos* and *Best of each group* (the fastest first pass) |
| **O** | open in Photoshop (a RAW opens in Camera Raw) |
| **R** | show in Finder |
| **?** | this list |

   The **Show** menu narrows things down: not decided yet, picks, rejects, rated 3★ and up, possible problems, eyes
   closed, blurry.
3. Click **Copy picks to Selects…**. You see every file that will be copied before anything happens. Each RAW is
   copied with its JPEG. A RAW also gets a small `.xmp` file holding its star rating, which Bridge, Camera Raw and
   Lightroom show.

Reopening a shoot is instant: only new or changed photos are analysed, and your picks and stars are remembered.

### What the suggestions mean

- **Groups**: photos from the same camera taken within 1.5 s of each other (a burst, including the GH5's
  6K/4K Photo bursts), or within 45 s that look alike. Two bodies of the same model are kept apart by their card
  folders.
- **★ Suggested**: in each group, the photo with the sharpest faces (or the sharpest frame, if it has no people),
  everyone's eyes open, the most smiles, nobody missing, and good exposure. The badge says why. It's only a
  suggestion: nothing is picked for you.
- **Blurry** is judged against the rest of the shoot, by the sharpest part of the frame, so a sharp subject on a
  soft background isn't called blurry. Fog, water and night skies can still score low.
- **Eyes closed** looks at the main people in the frame, not small faces in the background.

### Files it understands

Sony `.ARW`, Panasonic `.RW2`, phone `.DNG`, and other RAWs (`.CR2/.CR3/.NEF/.RAF/.ORF`), plus JPEG, HEIC, PNG
and TIFF. A RAW and its JPEG twin are one photo. Photo Cull judges the JPEG when there is one, otherwise the
preview the camera stores inside the RAW. RAW files are never fully decoded, which keeps it fast.

### Google Drive

Shoots inside a Google Drive folder work, including online-only files: Drive downloads each file as Photo Cull
reads it, and the progress shows *Downloading from Google Drive*. Where a JPEG twin exists, the much bigger RAW
isn't downloaded at all. If a file can't be fetched (offline), it's listed as unreadable and tried again next time.

Photo Cull keeps its database and thumbnails out of your shoot folder, in
`~/Library/Application Support/Photo Cull/jobs/`, so they never fight with Drive's syncing. Selects copies are
written once under their final names, so Drive uploads each finished file once. They do count towards your Drive
storage.

## How it's built

- `photocull/photos.py` finds the photos, pairs RAW and JPEG, and reads RAW previews and EXIF.
- `photocull/quality.py` measures sharpness (Laplacian, sharpest of 4×4 tiles) and exposure. It finds faces with
  YuNet and scores eyes and smiles with MediaPipe Face Landmarker blendshapes. It also describes each image with a
  small MediaPipe embedder, used to spot look-alikes.
- `photocull/job.py` holds one shoot: analysis (2 threads, low priority, so the laptop stays cool), grouping and
  suggestions, marks, and copying to Selects.
- `photocull/server.py` and `photocull/static/index.html` are the local page at http://localhost:8765. It only
  listens on this computer.
- `mac/` holds the installer, the app launcher and the icon. `scripts/build_zip.py` builds the download.

Everything runs on the CPU and is sized for a MacBook Neo (A18 Pro, 8 GB). The GitHub Actions test installs from
the zip on an Apple Silicon Mac, analyses a sample shoot and a set of full-size 24 MP photos, and reports speed and
memory in the run summary. Model and library licences, all of which allow commercial use, are listed in
[THIRD-PARTY.md](THIRD-PARTY.md).

### Developing (Mac or Windows)

```bash
uv sync
uv run python -m photocull        # then open http://localhost:8765
uv run pytest -q                  # generates a sample shoot (no real photos) and checks the analysis
python scripts/build_zip.py       # dist/PhotoCull-mac.zip
```

To publish a release, tag it (`git tag v0.1.1 && git push --tags`). The workflow tests it on a Mac and attaches the
zip to the release.
