# PrusaLink widget for Tesserae

Print status for an Original Prusa printer on an e-ink panel: what the printer
is doing, how far the print has got, when it will finish, and the nozzle and
bed temperatures.

It reads the printer's own PrusaLink API over your local network. No Prusa
account or Prusa Connect is involved.

![The widget at its largest size, showing a print at 42%](screenshots/lg.png)

> **Status: early.** Checked against one real printer, an MK3S+ running the
> Raspberry Pi PrusaLink 0.8.1, while idle: sign-in, state and temperatures
> work. A running print and the model preview have only been exercised
> against the mock printer in this repository, and no printer with PrusaLink
> built in has been tried yet. Reports are welcome.

## Folders shipped

- `prusalink_status`

## Setting it up

In Tesserae, open the widget's settings and enter the printer's address and
either a username and password, or an API key.

**Printer with PrusaLink built in** (MK4 family, MK3.5, MINI, XL, CORE One):

1. On the printer, open **Settings, Network** and note its IP address.
2. Open **Settings, Network, PrusaLink** and note the password.
3. Leave the username as `maker`.

**Raspberry Pi PrusaLink** (MK3 and MK2.5 families):

1. Use the username and password you chose when you set PrusaLink up.
2. Or copy the API key from PrusaLink's own web page, under **Settings**, and
   leave the password empty.

Prefer the printer's IP address (for example `http://192.168.1.50`) over a
name like `prusalink.local`. Tesserae often runs in Docker or Home Assistant,
where `.local` names can't be looked up, and the card then reads "Offline".

## What it shows

| Printer state | Card |
|---|---|
| Printing | Progress ring, file name, time left, finish time, elapsed |
| Paused, needs attention | Progress ring, time left, elapsed |
| Finished, stopped | Progress ring, elapsed |
| Idle, ready, busy, error | State and a one-line hint |
| Switched off or unreachable | "Offline" (not an error: printers get turned off) |

Nozzle and bed temperatures sit underneath, with the target while heating.
The largest size adds Z height and print speed.

The two larger sizes also show the model preview: the picture your slicer
saves inside the file. The widget downloads it from the printer once per
file. Files sliced without a PNG preview show no picture, and the option
"Show the model preview" turns it off.

### Leading with the model

Set the cell option **Lead with** to **Model preview** and the picture
becomes the hero: it fills the card, with the state, percentage and a
progress bar underneath. The largest size keeps time left, finish time and
temperatures in a row below the bar.

![The same print with the model preview leading](screenshots/hero-lg.png)

When nothing is printing, or the file has no picture, the card falls back to
the standard layout.

The widget shows an error only for things you need to fix: no address,
nothing to sign in with, or a sign-in the printer refuses.

## Developing against the mock printer

`mock/prusalink_mock.py` is a stand-in printer, standard library only:

```sh
python3 mock/prusalink_mock.py --scenario printing --live
```

It listens on `http://127.0.0.1:8017` with the password `mock`. Point the
widget's settings at that and it renders as if a printer were there.

Change what the printer is doing while it runs:

```sh
curl -X POST http://127.0.0.1:8017/_mock/scenario/paused
```

Scenarios: `idle`, `ready`, `busy`, `printing`, `paused`, `attention`,
`finished`, `stopped`, `error`. Stop the mock to see the offline card.

Useful flags: `--progress 80`, `--duration 3600`, `--no-thumbnail`,
`--api-key KEY` for a key separate from the password, `--auth digest` or
`--auth apikey` to allow one sign-in method only, `--digest-algorithm MD5`.

The mock's field names and types follow
[Prusa's OpenAPI document](https://github.com/prusa3d/Prusa-Link-Web/blob/master/spec/openapi.yaml).
Its values are invented, and its model preview is a plain orange block.

## Tests

```sh
python -m pytest -q
```

The data tests run on their own. The render tests need a Tesserae checkout:
set `TESSERAE_REPO=/path/to/tesserae`, or keep this repository at
`../widgets/<this repo>` beside it. Without one they are skipped. Run them
with the Tesserae checkout's Python so its dependencies are available.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
