# Signing — proving who issued the evidence, and how to verify it

This document exists so that a third party can **verify** the credential
evidence packet cryptographically, and so the maintainer sets signing up once,
correctly. It records one deliberate decision up front:

> **We sign the packet's *content*, not the evidence *tag*.**

The evidence anchor `credential-packet-v1` is a *lightweight* git tag (it points
straight at a commit). Git can only GPG-sign *annotated* tags, so signing that
tag would mean deleting and recreating it — which the tag-protection ruleset
(`protect-release-tags`) exists precisely to forbid. Signing the packet bytes
with a detached signature gives a stronger property (it binds the actual
evidence content, not a git pointer) **without** touching the immutable tag.
Future *version* tags can still be signed going forward; the historical
lightweight tags are left as-is.

## Prerequisites (one-time)

GitHub shows a **Verified** badge only when the key's UID email is an email you
have **verified on your GitHub account** *and* it matches your git commit email.
Check `Settings → Emails` first and decide which email to use.

```bash
# 1. install gpg
brew install gnupg

# 2. generate a key (interactive: choose (9) ECC -> (1) ed25519;
#    use your GitHub-verified email; set a passphrase)
gpg --full-generate-key

# 3. find the key id (the string after `ed25519/` on the `sec` line -> <KEYID>)
gpg --list-secret-keys --keyid-format=long

# 4. export the public key (copy the whole block, BEGIN..END included)
gpg --armor --export <KEYID>
```

Then in the browser: `Settings → SSH and GPG keys → New GPG key` → paste the
exported public key → save. Finally point git at the key:

```bash
# 5. tell git which key to use; the email must match the key's UID
git config --global user.signingkey <KEYID>
git config --global user.email "<your-GitHub-verified-email>"
```

## Signing the evidence packet (recommended — does not touch any tag)

The packet asset on the `credential-packet-v1` release is
`credential_packet_v1.tar.gz`.

```bash
# download the published asset
gh release download credential-packet-v1 \
  -R haeliotang/agent-runtime-observatory -D /tmp/pkt
cd /tmp/pkt

# produce a detached, ASCII-armored signature over the exact bytes
gpg --armor --detach-sign credential_packet_v1.tar.gz

# attach the signature as a new asset on the same release
gh release upload credential-packet-v1 credential_packet_v1.tar.gz.asc \
  -R haeliotang/agent-runtime-observatory
```

### How a third party verifies it

```bash
gh release download credential-packet-v1 \
  -R haeliotang/agent-runtime-observatory -D pkt && cd pkt
gpg --recv-keys <KEYID>                       # fetch the maintainer's public key
gpg --verify credential_packet_v1.tar.gz.asc credential_packet_v1.tar.gz
# "Good signature" => these bytes were signed by <KEYID> and are unmodified
```

This is independent of, and complementary to, the packet's own self-verifying
sha256 chain (see [evidence-matrix.md](evidence-matrix.md)): the sha256 proves
*integrity* (nothing changed), the signature proves *authorship* (who issued it).

## Signing future version tags (optional)

Historical `v*` tags are lightweight and left untouched. From the next release
on, cut annotated *signed* tags so GitHub renders them Verified:

```bash
git tag -s v0.2.6 -m "..."          # -s = signed annotated tag
# or make it the default:
git config --global tag.gpgSign true
```

## What this does and does not establish

| Property | Signing gives | Signing does **not** give |
|---|---|---|
| **Authorship** | proof the packet bytes were signed by the holder of `<KEYID>` | proof of *who the human behind the key is* — that rests on GitHub's email verification and your key hygiene |
| **Content integrity** | detached signature fails if a single byte changes | protection of assets that were never signed |
| **Tag immutability** | unchanged — signing content leaves `credential-packet-v1` exactly as protected by the ruleset | retroactive signing of the existing lightweight evidence tag (deliberately not done) |

See also: [Immutable releases](limitations.md) is a separate, browser-only repo
setting that, when available, makes published release assets undeletable — a
useful complement to signing, not a substitute for it.
