import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

if (process.platform !== 'linux') {
  console.error(
    'Visual baselines must be updated on Linux using the same Playwright ' +
    'runtime as GitHub Actions.'
  )
  process.exit(1)
}

const playwrightCli = fileURLToPath(
  new URL('../node_modules/@playwright/test/cli.js', import.meta.url)
)
const result = spawnSync(
  process.execPath,
  [playwrightCli, 'test', '--update-snapshots'],
  { stdio: 'inherit' }
)

if (result.error) {
  throw result.error
}
process.exit(result.status ?? 1)
