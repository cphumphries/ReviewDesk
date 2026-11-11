# Upgrading ReviewDesk

These steps move you from an earlier version of ReviewDesk to a new one. They take a few minutes, and none of the
work in your projects is lost or changed by them.

## Before you start: agree a day with your team

**Everyone in a team moves to the new version on the same day, the project lead first, and nobody goes back to the
old one.** An older version cannot read some things the new one records, such as an "Ongoing study" decision or the
newer risk-of-bias tools, and shows "Something went wrong" when it meets them. So agree a day with everyone who
works on the project. On that day:

1. **Everyone closes ReviewDesk** on every computer they use for the project, and waits until OneDrive (or the sync
   service you use) shows that it has finished.
2. **The project lead upgrades first**, by the steps below, and opens each project with the new version. ReviewDesk
   then records in the project that it needs the new version. The lead waits until OneDrive has finished again.
3. **Everyone else upgrades**, on every computer they use for the project, and opens the project.

If someone in the team last used an older version, ReviewDesk warns you.

From version 1.3 on, ReviewDesk also protects a project from an older version. Once the project lead has opened a
project with a version whose records an older one cannot read, the older one refuses to open that project and says
which version to download. And once anyone in the team has worked on a project with such a version, an older one
that still has the project open stops saving in it: it can still be read there, and its reports downloaded. Both
depend on the sync service: an older ReviewDesk learns that a newer one has been used only when its computer has
received the project's files as they now are. That is why everyone closes ReviewDesk first. (Versions 1.2 and
earlier do not check at all, which is why nobody should go back to them.)

The move from 1.3.0 to 1.3.1 is made this way too. From 1.3.1, every fingerprint the data checks keep is written
out in full, where earlier versions kept only its start, and 1.3.0 cannot check those: to 1.3.0, a row written by
1.3.1 looks like a row changed outside ReviewDesk. Nothing in the project is written again by the move: the rows
and the seal that 1.3.0 wrote stay as they are and are checked as before, and what is added from then on is whole.

## The steps, on each computer

1. **Download the new version** from the [Releases](../../releases) page: `ReviewDesk-x.y.z-windows.zip` on Windows,
   `ReviewDesk-x.y.z.zip` on a Mac or on Linux.
2. **Close the old ReviewDesk.** Close its window (the black window on Windows, the Terminal window on a Mac), or
   press Ctrl+C in it. The new version will not start while the old one is running.
3. **Extract the new version to a new folder.** Do not copy it over the old folder. On Windows, right-click the zip,
   choose **Extract All**, then **Extract**.
4. **Start the new version** from the new folder, as the first time: double-click **Start ReviewDesk.bat** on
   Windows, **Start ReviewDesk (Mac).command** on a Mac, or run `sh start-reviewdesk.sh` on Linux. If you made a
   shortcut to the old start file, make it again for the new one.
5. **Check that it is the new version**: the version number is shown at the bottom of the menu. Your project opens
   as before.
6. **Delete the old ReviewDesk folder** once the new version works.

Your settings, your AI key, the list of your projects and this computer's own copies of project files are kept: they
are stored in your user account, not in the ReviewDesk folder, and every version uses the same ones. Your projects
stay in their shared folders, where they were.

## After upgrading a project begun with an earlier version

When the project lead first opens a project begun with an earlier version, ReviewDesk may ask for the risk-of-bias
plan (which tool is used for each study design the review includes). The project lead records it when ReviewDesk
asks, under **Project set-up**; reviewers cannot add risk-of-bias assessments until it is recorded.

## If something goes wrong

Do not go back to the old version: it may misread what the new one has recorded. Keep using the new version and
open an issue on this repository saying what you see (never attach a real review's records). The old folder is not
needed to undo anything: everything the review needs is in the project folder.

If ReviewDesk 1.3.0 was used on a project after someone had opened it with 1.3.1 (its computer had not yet received
the newer files, or someone went back to it), its data checks may list the rows that 1.3.1 wrote as "changed or
added outside ReviewDesk". They were not: do not choose **Put back this computer's copy** or **This change is
correct** for them in 1.3.0. Close it and upgrade. If someone did choose one, no vote or answer is lost. Each person
opens the project with 1.3.1 on the computer they used: ReviewDesk puts that computer's own rows back by itself, and
the data checks on the Overview then list anything that still needs **Put back this computer's copy** (the AI's
answers are the one kind of row that is not put back automatically).
