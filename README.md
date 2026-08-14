# Sunday Light for Home Assistant

 *This is a side project entirely vibe-coded and not officially supported by Sunday light.
 Please create any issues in Github.*

Control your [Sunday](https://sundaylight.cc) SL1 light from Home Assistant:
on/off, brightness, colour temperature (2650–6000 K) and transitions, with
your Sunday rooms suggested as Home Assistant areas.

## How it works

The integration talks to the Sunday cloud — the same API the Sunday app uses.
There is no local control path on the SL1 today, so:

- An internet connection is required for control.
- State is polled (every 10 seconds by default, configurable). Changes made
  from the Sunday app, remote, or schedules appear within one polling
  interval.

Schedules, circadian protocols and remote pairing stay in the Sunday app.
Home Assistant automations and Sunday schedules can coexist — whichever wrote
last wins, exactly as with the app today.

## Requirements

- A Sunday account with at least one claimed SL1 light (set up in the Sunday
  app first).
- Home Assistant 2025.1 or newer.

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/natmart-in/ha-sunday-light` with category
   **Integration**.
3. Install **Sunday Light (Beta)** and restart Home Assistant.

### Manual

Copy `custom_components/sunday_light/` into your Home Assistant
`config/custom_components/` directory and restart.

## Setup

1. **Settings → Devices & Services → Add Integration → Sunday Light.**
2. Sign in with your Sunday email and password (the same credentials as the
   app). Only a long-lived sign-in token is stored — never your password.
3. Your lights appear as devices, one light entity each, with their Sunday
   room suggested as the area.

Privacy-conscious alternative: create a separate Sunday account, invite it to
your home as a **guest** from the Sunday app, and sign in to Home Assistant
with that. Guests can control lights but cannot see home members or
schedules.

## Options

- **Polling interval** (5–120 s, default 10): Settings → Devices & Services →
  Sunday Light → Configure.

## Entities

Each SL1 becomes one `light` entity supporting:

| Feature | Range |
| --- | --- |
| On/off | — |
| Brightness | 1–100 % (the SL1's minimum dim level is 1 %) |
| Colour temperature | 2650–6000 K |
| Transition | any duration, default 0.25 s |

Lights that are offline (powered down at the wall, Wi-Fi lost) show as
unavailable.

## Roadmap

- Diagnostic sensors (Wi-Fi signal, temperatures, fan/pump, power draw)
- Push state updates (removing the polling delay)

## Disclaimer

Not affiliated with Home Assistant. Use at your own risk; the beta label is
meant sincerely.

## License

[MIT](LICENSE)
