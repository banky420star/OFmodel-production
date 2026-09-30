#!/usr/bin/env node
/**
 * Pack the app's logo into storage/AppIcon.icns.
 *
 *   node /Volumes/AI_DRIVE/persona/scripts/make_launch_icon.mjs
 *
 * The source is the app's own shipped artwork, `apps/web/public/icon-512.png` —
 * the lime "P" monogram on the app's near-black `#0d0d10` — not a redrawing of
 * it. It is two flat colours and bleeds to its own edges, so the only thing done
 * to it here is the macOS icon silhouette: the corners are rounded, which trims
 * the top-left and bottom-left of the P's stem exactly as it trims any
 * full-bleed icon. Nothing is recoloured or redrawn.
 *
 * Chromium (borrowed from the web app's Playwright, resolved by absolute path so
 * this runs from any directory) does the rounding, because it antialiases the
 * arc. The artwork is 512px and every size the iconset asks for divides it
 * exactly — 256, 128, 64, 32, 16 are 2:1 … 32:1 downsamples — so nothing is
 * invented by resampling. The one exception is the 1024 slot (512@2x), which is
 * a 2x upscale because no larger artwork exists; macOS only reaches for that
 * size when previewing, and flat two-colour art takes a 2x upscale well.
 */
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdirSync, rmSync, statSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = dirname(HERE)
const LOGO = join(REPO, 'apps', 'web', 'public', 'icon-512.png')
const ICNS = join(REPO, 'storage', 'AppIcon.icns')
const PNG_PREVIEW = join(REPO, 'storage', 'AppIcon.png')
const ICONSET = '/tmp/persona_icon.iconset'
const MASTER = '/tmp/persona_icon_master.png'
const WEB_MODULES = join(REPO, 'apps', 'web', 'node_modules') + '/'

// The macOS app-icon silhouette as a fraction of the side (Big Sur squircle,
// approximated by a plain rounded rectangle — what every icon tool ships).
const RADIUS_FRACTION = 0.2237

if (!existsSync(LOGO)) {
  console.error(`missing the logo at ${LOGO}`)
  process.exit(1)
}

const require = createRequire(WEB_MODULES)
const { chromium } = require('playwright')

rmSync(ICONSET, { recursive: true, force: true })
mkdirSync(ICONSET, { recursive: true })

// The artwork goes in as a data URI, not a file:// src. A file:// subresource
// from a setContent page (origin about:blank) is blocked by Chromium, and the
// failure is silent in the worst way: it paints the broken-image placeholder,
// which screenshots as an almost-empty transparent tile that still packs into a
// perfectly valid .icns. That is exactly the shape of "the file exists so it
// must be fine", so `assertLime()` below checks the pixels instead of trusting
// that the file was written.
const logoDataUri = 'data:image/png;base64,' + readFileSync(LOGO).toString('base64')
const radius = Math.round(512 * RADIUS_FRACTION)

const browser = await chromium.launch()
try {
  // Native resolution, 1:1 — the master is never upscaled here.
  const page = await browser.newPage({ viewport: { width: 512, height: 512 }, deviceScaleFactor: 1 })
  await page.setContent(`<html><body style="margin:0;background:transparent">
    <img id="logo" src="${logoDataUri}" width="512" height="512"
         style="display:block;border-radius:${radius}px">
  </body></html>`)

  await page.screenshot({ path: MASTER, omitBackground: true })

  // Verify the artifact that was actually written, not the page it came from.
  // Sampling the live <img> through a canvas would prove nothing: drawImage
  // ignores CSS clipping, so it reports the raw artwork and the corner would
  // read as opaque no matter what the border-radius did.
  const masterUri = 'data:image/png;base64,' + readFileSync(MASTER).toString('base64')
  const sample = await page.evaluate(async (uri) => {
    const img = new Image()
    img.src = uri
    await img.decode()
    const c = document.createElement('canvas')
    c.width = c.height = img.naturalWidth
    const ctx = c.getContext('2d')
    ctx.drawImage(img, 0, 0)
    const at = (x, y) => Array.from(ctx.getImageData(x, y, 1, 1).data)
    return {
      centre: at(img.naturalWidth >> 1, img.naturalHeight >> 1),
      corner: at(2, 2),
      size: [img.naturalWidth, img.naturalHeight],
    }
  }, masterUri)

  if (sample.size[0] !== 512 || sample.size[1] !== 512) {
    throw new Error(`the master came out ${sample.size.join('x')}, not 512x512`)
  }

  // The brand lime is #d9fb71 = (217, 251, 113): green is the strongest channel
  // and blue the weakest, but red is high too, so the test is the channel
  // ordering rather than "looks green". A centre that is transparent or grey
  // means the artwork never rendered and a blank icon is about to be packed.
  const [r, g, b, a] = sample.centre
  if (!(a === 255 && g > 200 && b < 160 && g > r && r > b)) {
    throw new Error(
      `the centre pixel is not the brand lime #d9fb71 (got ${sample.centre}) — the artwork did not render`
    )
  }

  if (sample.corner[3] !== 0) {
    throw new Error(
      `the corner is opaque (${sample.corner}) — the icon silhouette was not applied`
    )
  }
} finally {
  await browser.close()
}

// iconutil's naming: each point size at 1x and/or 2x, as icon_<pt>x<pt>[@2x].png.
const SLOTS = [
  [16, 1], [16, 2], [32, 1], [32, 2],
  [128, 1], [128, 2], [256, 1], [256, 2], [512, 1], [512, 2],
]

for (const [pt, scale] of SLOTS) {
  const px = pt * scale
  const name = `icon_${pt}x${pt}${scale === 2 ? '@2x' : ''}.png`
  execFileSync('/usr/bin/sips', ['-z', String(px), String(px), MASTER, '--out', join(ICONSET, name)], {
    stdio: 'ignore',
  })
}

mkdirSync(dirname(ICNS), { recursive: true })
execFileSync('/usr/bin/iconutil', ['-c', 'icns', ICONSET, '-o', ICNS])

// A plain PNG of the mark, for docs, chat, or anywhere an .icns is useless.
copyFileSync(join(ICONSET, 'icon_512x512.png'), PNG_PREVIEW)

console.log(`wrote ${ICNS} (${statSync(ICNS).size} bytes)`)
console.log(`wrote ${PNG_PREVIEW}`)
