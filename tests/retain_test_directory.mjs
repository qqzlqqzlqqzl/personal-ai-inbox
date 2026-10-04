import { lstat, mkdir, rename } from 'node:fs/promises'
import { basename, dirname, join } from 'node:path'
import { randomUUID } from 'node:crypto'

// Retain generated test files outside their active path instead of deleting them.
// A failed move remains a test failure; there is no delete or copy/remove fallback.
export async function retainTestDirectory(directory) {
  const stat = await lstat(directory)
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Expected a real test directory')
  const retained = join(dirname(directory), 'reader-retained-test-directories')
  await mkdir(retained, { recursive: true })
  const destination = join(retained, `${basename(directory)}-${randomUUID()}`)
  await rename(directory, destination)
  console.log(`Retained test directory: ${destination}`)
  return destination
}
