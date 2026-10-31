# Photo Cull

Pick the best photos from one shoot, quickly. It runs on your Mac with nothing sent online and no subscription.

Choose a shoot's folder and Photo Cull looks at every photo in it. It groups bursts and look-alike shots and suggests
the best of each group. It flags photos that look blurry, have closed eyes, or are too dark or bright. You stay in
charge: you go through with the keyboard, pick and star what you want, and copy the picks to a **Selects** folder.

**Your originals are never changed or deleted.** Photo Cull only reads them. It writes inside your shoot folder only
when you ask: copies in a Selects folder (never overwriting a photo there), and rejects moved into a Deletes folder,
which you can put back.

> **Early days on a real Mac.** Photo Cull is tested automatically on GitHub's virtual Apple Silicon Macs
> (macOS 15 and 26): installing from the zip, opening the app, analysing sample photos, and the review page's controls
> through its API. On a real MacBook Neo the installer has run end to end, and that's how we found and fixed the
> "install Rosetta?" prompt (fixed in v0.1.2). The folder picker, the Photoshop hand-off and a real shoot from Google
> Drive haven't been tried there yet. Please report anything odd.

## Install (Mac)

1. Download **PhotoCull-mac.zip** from the [latest release](../../releases/latest). Your Mac unzips it into
   Downloads.
2. Double-click **Install Photo Cull**.
3. The first time only, macOS says it can't check it, because it isn't from the App Store. Click **Done**, then
   open **System Settings → Privacy & Security**, scroll down, click **Open Anyway** and confirm. Removing this step
   would need a paid Apple developer account.
4. Click **Install**. It takes a few minutes the first time. When it's done, click **Open Photo Cull**.

After that, open **Photo Cull** from Applications or Launchpad. It opens in your web browser. No Terminal is needed
at any point.

**Updates:** when there's a new version, Photo Cull shows an **Update** button at the top. Clicking the version
number next to it checks straight away. One click: it closes,
updates itself and reopens, keeping your stars and picks. To check for updates, Photo Cull asks GitHub for the
latest version number at most every 20 minutes. Nothing about your photos is ever sent.

For Joel or anyone comfortable with Terminal, this one line also installs or updates:
`curl -fsSL https://github.com/joelyoungman/photo-cull/releases/latest/download/install.sh | bash`

To remove Photo Cull, delete it from Applications and delete the folder
`~/Library/Application Support/Photo Cull`. Your photos and Selects folders stay.

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
| **S** (or hover Sharpness) | show which parts are in focus (green glow) |
| **B** | switch between *All photos* and *Best of each group* (the fastest first pass) |
| **O** | open in Photoshop: the copy in Selects once it's there (a RAW opens in Camera Raw) |
| **R** | show in Finder |
| click, **⌘/Ctrl-click**, **Shift-click** | select one, add or remove one, select a range (**⌘/Ctrl-A** selects all, **Shift ← →** extends) |
| **?** | this list |

   The **Show** menu narrows things down: not decided yet, picks, rejects, rated 3★ and up, possible problems, eyes
   closed, blurry.
   P, X, U and the star keys mark every selected photo at once.
3. Click **Copy picks to Selects…**. You see every file that will be copied before anything happens. Each RAW is
   copied with its JPEG. A RAW also gets a small `.xmp` file holding its star rating, which Bridge, Camera Raw and
   Lightroom show. Any Camera Raw / Photoshop edits already saved in a RAW's `.xmp` file come along, and copying
   again refreshes them if you've edited the original since. Edits you later make to the Selects copy are never
   overwritten.
4. To clear out the rejects: **Move rejects to Deletes…** moves the photos marked X (RAW, JPEG and edits together)
   into a **Deletes** folder inside the shoot, after showing you the list. Nothing is deleted. **Undo**, or
   **Put back from Deletes** later, moves them back; delete the Deletes folder yourself once you're sure.
5. Then work from Selects: **Open Selects folder** shows it in Finder, and **Picks in Photoshop** opens all the copies
   there in Photoshop (Camera Raw for RAWs).

Reopening a shoot is instant: only new or changed photos are analysed, and your picks and stars are remembered.
Added more photos to the folder? Photo Cull looks for them when you come back to its page, or click **Check for new
photos** at the top.
Previews of shoots you haven't opened for 60 days are cleared to save disk space. Your marks stay, and the previews
are remade if you open the shoot again.

### Excluding a person

If someone asks not to be in any photos: click **Exclude a person…** (or turn on **Faces**), then click their face in a
photo where it's clear. Photo Cull finds every photo they're in, including in the background, and shows them as a list
of face crops to check. Untick any that aren't them, tick any "maybe" that are, then **Mark ticked photos X**. Those
photos get an **Excluded person** badge, so **Move rejects to Deletes** sets them aside. Photos added to the shoot
later are checked too. **Excluded (n)** lists the people excluded from the shoot; **Stop excluding** puts each photo
back to the mark it had before. Faces are only compared within the shoot, on this Mac; nobody is identified by name.
The face-matching model (39 MB) downloads the first time you use this.

### What the suggestions mean

- **Sharpness 0–100**: how sharp a photo is compared with the rest of this shoot (90 = sharper than 90% of it).
  Photos of people are judged on their eyes. It's shown on the photo (green / amber / red) and on each thumbnail, and
  **Order: sharpest first** lists the shoot from sharpest to softest. It's a guide, not a verdict: a soft photo can
  still be the keeper.
- **Which parts are sharp**: hover over the Sharpness badge, or press **S** to keep it on, and the in-focus parts of
  the photo glow green, like focus peaking on a camera. A blurry photo barely glows at all.

- **Groups**: photos from the same camera taken within 1.5 s of each other (a burst, including the GH5's
  6K/4K Photo bursts), or within 45 s that look alike. Two bodies of the same model are kept apart by their card
  folders.
- **★ Suggested**: in each group, the photo with the sharpest eyes (or, with no people, the sharpest spot where the
  camera focused, or the sharpest frame), everyone's eyes open, the most smiles, nobody missing, and good exposure.
  Focus comes first: a frame whose subject is soft is never suggested, however good the smile, and is marked
  **softer than the others**. If every frame of a group is soft there's no suggestion; the sharpest one still shows
  in **Best of each** so the moment isn't hidden. The badge says why. It's only a suggestion: nothing is picked for you.
- **How focus is judged**: on the eyes, measured on the photo at full preview size (a camera JPEG is read at about
  3000 px wide), so a slightly soft face isn't hidden by shrinking it. Sony and Panasonic cameras record where they
  focused; the face at that spot is the subject. High-ISO grain is measured and taken out first, so it doesn't pass
  for detail, and each frame is compared with the same person in the other frames of its burst. A RAW without its
  JPEG twin is judged on its built-in preview (about 1600 px for the A7 III), which can miss slight misfocus.
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

- `photocull/photos.py` finds the photos, pairs RAW and JPEG, and reads RAW previews, EXIF (ISO too, including
  Panasonic RW2's own ISO tag) and where Sony and Panasonic cameras focused (from their maker notes).
- `photocull/quality.py` measures sharpness (Laplacian, sharpest of 4×4 tiles; for focus, the eyes and the
  camera's focus spot, with grain taken out) and exposure. It finds faces with YuNet and scores eyes and smiles with
  MediaPipe Face Landmarker blendshapes. It also describes each image with a
  small MediaPipe embedder, used to spot look-alikes.
- `photocull/job.py` holds one shoot: analysis (2 threads, low priority, so the laptop stays cool), grouping and
  suggestions, marks, and copying to Selects.
- `photocull/server.py` and `photocull/static/index.html` are the local page at http://localhost:8765. It only
  listens on this computer.
- `mac/` holds the installer (`installer.applescript` → the double-click app, `install-mac.sh` does the work),
  the launcher and the icon. `install.sh` is the online installer behind the Update button. `scripts/build_zip.py` builds
  the download (on a Mac, which CI does).

Everything runs on the CPU and is sized for a MacBook Neo (A18 Pro, 8 GB). Analysis never uses the whole processor:
it runs 2 workers (measured at about 2 cores busy at most), at low priority. On a Mac they're marked as background
work, so macOS keeps the fast cores free for the screen and browser and slows the analysis if the laptop gets warm.
The Mac test fails if analysis keeps more than 2.5 cores busy. The GitHub Actions test installs from
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
