# Brief for the blind writer of `held_out_month2b`

**For:** a writer who has never seen tier0's code, its prompts, its tests or any earlier set of messages. **Written:** 2026-11-18, before the set exists, and committed with its pass rules (`docs/plans/milestone-3e-month2b-rules.md`).

## What you write

One JSON file of **72 message templates** for an IT help desk in Slack, at a fictional company, Meridian: 230 people, fully remote, in 14 countries, with an IT team of 3. Every name, address and detail is made up.

Each template is one message an employee might send to the IT bot. A program later fills in names, adds a little Slack noise, and turns the templates into a month of tickets.

Write it to the path you are given, and nowhere else, in this exact shape:

```json
{"templates": [
  {"id": "B001", "kind": "chitchat", "lang": "en", "text": "...", "expected_guide": null},
  ...
]}
```

- `id`: `B001` to `B072`, unique.
- `kind`: one of the 4 kinds below.
- `lang`: `en`, `de`, `es` or `fr`. The text is in that language.
- `text`: the message, as a person would type it in Slack. One to four sentences. Plain text.
- `expected_guide`: the guide id for a `how_to_guide` template, `null` for the other kinds.
- The only placeholder allowed is `{colleague}`: another person's full name. Use no other braces.

## The 4 kinds, 18 templates each

| Kind | What the message is | What should happen |
|---|---|---|
| chitchat | The sender doesn't ask IT for anything at all. A hello, a thank-you for earlier help, a remark, a joke, a check that the bot is there, a goodbye before a holiday. | a person sees it; no guide is sent |
| hardware | A fault in a device the sender uses: a laptop, monitor, dock, keyboard, headset, phone, printer or other equipment is broken, damaged, failing, too slow, too hot, too loud, or behaving wrongly. | a person handles it |
| app_issue | A fault in software: an app or system shows an error, crashes, freezes, won't open, won't sync, won't save, logs the sender out, or is too slow to use. | a person handles it |
| how_to_guide | A question asking how the sender can carry out a task on their own, which one of the guides below answers. Set `expected_guide` to that guide's id. | answered with that guide |

- **Languages:** 54 English, 6 German, 6 Spanish, 6 French, spread over the 4 kinds.
- **`{colleague}`:** in at most 6 templates, where another person comes up naturally.
- **Guides:** the 18 `how_to_guide` templates use at least 14 different guides, none more than twice.

## What makes a good set

- **Write the way people write in Slack:** short or long, calm or stressed, precise or vague. Vary the order of facts, the tone and the length.
- **Vary what a fault report includes.** Some give only the symptom. Others give some of these: when it started, how often it happens, what the sender did about it, what they need to get done, and what they would like IT to do.
- **Make the hard cases hard and honest.** The line between a fault report and a how-to question must be clear to a careful IT person, but close:
  - a how-to question whose words sound like a complaint;
  - a fault report on the same topic as one of the guides;
  - a message that mentions a device or an app but asks for nothing.
- **The kind must be clear to a careful IT person** who reads the message. If you are unsure which kind it is, rewrite it until you are sure.
- **Every message is new.** Don't copy, translate or adapt messages from anywhere you have seen them.

## The guides (id | title | what it covers)

Only these 30 exist. A `how_to_guide` message must be one that its guide answers, judged from the title and summary.

| Id | Title | What it covers |
|---|---|---|
| KB-001 | Connect to the office Wi-Fi | Join the staff Wi-Fi network Meridian-Staff on a work laptop or phone in any Meridian office. |
| KB-002 | Get a visitor onto the guest Wi-Fi | Give a visitor internet access with a one-day code for the Meridian-Guest network. |
| KB-003 | Set up the VPN app for the first time | Install and sign in to the Meridian VPN app so you can reach internal sites from outside the office. |
| KB-004 | VPN is connected but internal sites don't load | What to check when the VPN shows connected and the intranet or other internal sites still time out. |
| KB-005 | Add an office printer on a Mac | Add the office follow-me printer queue to a work Mac. |
| KB-006 | Add an office printer on Windows | Add the office follow-me printer queue to a work Windows laptop. |
| KB-007 | Scan a document to your email | Scan paper on an office printer and receive it as a PDF in your work mailbox. |
| KB-008 | Book a meeting room | Reserve a meeting room from Google Calendar and check in at the room's panel. |
| KB-009 | Start a video call in a meeting room | Use the room's screen, camera and microphone for a Google Meet call. |
| KB-010 | Set up work email on your phone | Add your work Google account to a phone inside the managed work profile. |
| KB-011 | Turn on an out-of-office reply | Set an automatic reply in Gmail for days when you are away. |
| KB-012 | Use the standard email signature | Create a Gmail signature that follows Meridian's template. |
| KB-013 | Share a Google Drive file with someone outside Meridian | The rules and steps for sharing a file or folder with a customer or partner. |
| KB-014 | Get back a deleted Google Drive file | Restore a file you deleted from My Drive within the last 30 days. |
| KB-015 | Install approved software | Install apps that IT has already approved, without admin rights. |
| KB-016 | Install operating system updates | Run macOS or Windows updates when your laptop asks for them. |
| KB-017 | Laptop is slow: first checks | Simple checks to try when a work laptop feels slow or the fan runs all the time. |
| KB-018 | Set up your new laptop on day one | What happens the first time you switch on a new work laptop, and what you need to do. |
| KB-019 | Return or swap equipment | How to hand back a laptop, phone or accessory you no longer need. |
| KB-020 | Save and share team logins in the password manager | Use Meridian Vault to store logins for shared tools and share them with your team safely. |
| KB-021 | Join, create and name Slack channels | Find and join channels, and the naming rules for creating a new one. |
| KB-022 | Set Slack notifications and quiet hours | Choose what Slack notifies you about and pause notifications outside working hours. |
| KB-023 | Fix microphone or sound in a video call | Checks for when people can't hear you, or you can't hear them, in Google Meet or a Slack huddle. |
| KB-024 | Connect a second monitor or a dock | Plug a work laptop into a desk monitor or docking station and arrange the screens. |
| KB-025 | Order a keyboard, mouse or headset | Order standard accessories from the IT catalogue. |
| KB-026 | Work from another country for a short time | The IT checklist before you travel and work from abroad for up to four weeks. |
| KB-027 | Report a suspicious email | Report an email that looks like phishing with the Report button in Gmail. |
| KB-028 | Use a USB drive for work files | The rules for removable storage, and how to get an encrypted drive. |
| KB-029 | Sign in to Chrome with your work profile | Keep work and personal browsing apart by using a work profile in Chrome. |
| KB-030 | Ask IT for help the right way | Where to ask, the IT desk hours, and what to include so a request is solved the first time. |

## What you must not do

- Don't read any other file. You need only this brief.
- Don't include real people, companies, addresses, phone numbers, passwords or links. Use `example.com` if a link is needed.
- Don't tell anyone the wording when you're done. Report only counts: templates per kind and per language, `{colleague}` uses, how many different guides you used and the most uses of one guide.
