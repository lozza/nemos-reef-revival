# Game status and playing

[All guides](README.md)

The server is in early development. It has run on a real Fire 7, but gameplay
is still being reconstructed. Back up your reef before updating.

For the changes we've made to the original game, see
[Changes from the original game](CHANGES.md).

## Separate desktop prototype

The [Windows/Linux desktop prototype](DESKTOP.md) is experimental. Original
Windows gameplay and fullscreen have been checked on developer builds; Linux
has not been verified. The measured Windows scene runs at roughly 3 FPS, and
extended desktop gameplay remains unverified. There is no public desktop
release yet. The features below describe the Android/server project.

The [website countdown](https://nemos-reef-revival.pages.dev/) is counting down
to the Windows version. It does not announce a verified Linux release.

## Implemented

- Login, saved reefs, buying, placing, feeding, moving and collecting.
- The original starter reef.
- Friends: add by name, visit reefs and help each other.
- Story: 210 original quests plus 36 time-event quests, with original IDs and text.
- Level-based shop locks, Get It Early purchases and daily hidden-plant rewards.
- Treasure chests after their tutorial, and Squirt's Hide and Seek after “Let's Play!”.
- Vitality from plants and fish, with level caps; upgraded plant Patches from about level 10.
- Mr. Ray's daily homework from about level 9.
- Seasonal events on their dates, and other events rotating on weekends from level 6.

- Revival balance: reduced growth costs, larger slow-producer harvests,
  story/level-up pearls, affordable rare-fish feeding, improved mystery seeds
  and a capped sand-dollar/algae exchange. See [Revival economy](ECONOMY.md)
  and [balance examples](BALANCE.md). Longer-term pacing still needs player feedback.

## Still missing or imperfect

Co-op quests are not implemented. The Android Studio emulator can draw some
sand paths white; real devices have not shown that issue.

## Players and friends

Each device is a separate player. On first start, choose a profile name.
Names are unique; a number is added if a name is already taken.

The game's age choice affects its Kids edition: easier fish combinations,
guest-mission hints and three extra quests. It takes effect from the second
launch, after the client has reported its age group.

To add a friend, open **Friends → Invite** and enter their profile name.
Both players must use the same server. You can visit and help with five actions
per day, and view each other's sticker album and Vitality.

[Report a problem](TROUBLESHOOTING.md#reporting-a-game-problem) ·
[Back up your reef](SAVES.md)
