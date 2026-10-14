# Vendored front-end libraries

The UI is a single static page with no build step. These files are copied here (instead of loaded from a CDN)
so the hub works offline and does not leak your IP to third parties. Replace a file to upgrade it.

| File | Library | Version | License |
|---|---|---|---|
| `vue.global.prod.js` | [Vue 3](https://vuejs.org) | 3.4.21 | MIT |
| `xterm.min.js`, `xterm.min.css` | [xterm.js](https://xtermjs.org) | 5.3.0 | MIT |
| `xterm-addon-fit.min.js` | xterm.js fit addon | 0.8.0 | MIT |
| `xterm-addon-web-links.min.js` | xterm.js web-links addon | 0.9.0 | MIT |
| `tailwind.play.js` | [Tailwind CSS](https://tailwindcss.com) Play CDN build (browser JIT) | 3.x, fetched from `cdn.tailwindcss.com` | MIT |
| `fontawesome/` | [Font Awesome Free](https://fontawesome.com/license/free) | 6.4.0 | Icons CC BY 4.0, fonts SIL OFL 1.1, code MIT |

Source URLs were `cdn.jsdelivr.net/npm/<pkg>@<version>/...` and `cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/...`.
