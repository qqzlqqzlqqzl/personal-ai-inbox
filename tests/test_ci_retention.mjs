import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, readFile, lstat, symlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { retainTestDirectory } from './retain_test_directory.mjs'

const root = await mkdtemp(join(tmpdir(), 'reader-retention-contract-'))
const original = join(root, 'generated')
await mkdir(original)
await writeFile(join(original, 'sentinel.txt'), 'synthetic retained bytes\n')
const retained = await retainTestDirectory(original)
await assert.rejects(lstat(original), { code: 'ENOENT' })
assert.equal(await readFile(join(retained, 'sentinel.txt'), 'utf8'), 'synthetic retained bytes\n')
await assert.rejects(retainTestDirectory(original), { code: 'ENOENT' })
const link = join(root, 'link')
await symlink(retained, link)
await assert.rejects(retainTestDirectory(link), /Expected a real test directory/)
assert.equal((await lstat(link)).isSymbolicLink(), true)
const regular = join(root, 'regular.txt')
await writeFile(regular, 'keep regular file')
await assert.rejects(retainTestDirectory(regular), /Expected a real test directory/)
assert.equal(await readFile(regular, 'utf8'), 'keep regular file')
console.log('CI retention: moved bytes preserved; missing, symlink and regular-file inputs fail closed')
