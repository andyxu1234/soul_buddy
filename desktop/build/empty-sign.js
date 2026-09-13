// No-op code signer for local dev builds.
// electron-builder downloads/extracts winCodeSign (which contains macOS
// .dylib symlinks that Windows refuses to create without admin/DevMode).
// Supplying a custom sign function makes electron-builder skip winCodeSign
// entirely, producing an unsigned installer that is fine for local testing.
async function customSign(/* opts */) {
  // Intentionally do nothing — leave the executable unsigned.
  return;
}

module.exports = customSign;
module.exports.default = customSign;
