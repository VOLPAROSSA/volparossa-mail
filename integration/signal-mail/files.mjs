// SPDX-License-Identifier: GPL-3.0-only
import {
  closeSync, constants, fstatSync, fsyncSync, linkSync, lstatSync, openSync, readSync,
  realpathSync, unlinkSync, writeFileSync,
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';

export class MailError extends Error {}

export function privateDir(path) {
  const full = resolve(path);
  const st = lstatSync(full);
  if (!st.isDirectory() || full !== realpathSync(full) || st.uid !== process.getuid() || (st.mode & 0o077)) {
    throw new MailError('expected an owner-private directory');
  }
  return full;
}

export function read(path, maximum) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const st = fstatSync(fd);
    if (!st.isFile() || st.uid !== process.getuid() || (st.mode & 0o077) || st.size < 1 || st.size > maximum) {
      throw new MailError('input must be a nonempty, owner-private regular file within the size limit');
    }
    return readExact(fd, st.size);
  } finally { closeSync(fd); }
}

export function readExact(fd, size) {
  const bytes = Buffer.alloc(size);
  let offset = 0;
  while (offset < bytes.length) {
    const count = readSync(fd, bytes, offset, bytes.length - offset);
    if (!count) throw new MailError('input changed while reading');
    offset += count;
  }
  if (readSync(fd, Buffer.alloc(1), 0, 1)) throw new MailError('input changed while reading');
  return bytes;
}

export function syncDirectory(path) {
  const fd = openSync(path, constants.O_DIRECTORY);
  try { fsyncSync(fd); } finally { closeSync(fd); }
}

export function publish(path, bytes, profileId) {
  const parent = privateDir(dirname(path));
  // The caller holds the profile's write lock. One fixed slot per profile and
  // directory also recovers an interrupted link or a partial temporary write.
  const temporary = join(parent, '.signal-mail-' + profileId);
  if (resolve(path) === temporary) throw new MailError('reserved output name');
  // Unlink the reserved name atomically. A symlink here loses only its
  // directory entry; the target is never opened, truncated or removed.
  try { unlinkSync(temporary); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const fd = openSync(temporary, 'wx', 0o600);
  try {
    try { writeFileSync(fd, bytes); fsyncSync(fd); } finally { closeSync(fd); }
    try { linkSync(temporary, path); }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      if (!read(path, bytes.length).equals(bytes)) throw new MailError('existing output contains different bytes');
    }
  } finally { unlinkSync(temporary); }
  syncDirectory(parent);
}
