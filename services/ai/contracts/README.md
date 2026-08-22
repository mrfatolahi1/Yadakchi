# contracts/

The wire format this service agreed to. Defined by `docs/specs/02-EVENT-CONTRACTS.md`.

| Folder | Meaning |
|---|---|
| `published/` | JSON Schemas for topics **this service produces**. This service owns them; changes need human review (see `.github/CODEOWNERS`). |
| `consumed/` | Byte-identical copies of the schemas of topics **this service reads**. Never edit these by hand. |
| `examples/` | Realistic example payloads, usable as test fixtures. |

`consumed/` also holds `<publisher>-openapi.json` for every service this one
calls over HTTP — `make sync-contracts` vendors those from the publisher's
`published/openapi.json`, because a service may not read another service's
directory to fetch one. Who calls whom is declared in `platform/http/apis.yml`.

`make check-contracts` fails the build if any `consumed/` copy differs from the
publisher's `published/` file by so much as one byte. To change a contract:
edit the schema in the owning service, run `make sync-contracts`, commit both.

Which service publishes and consumes which topic is declared in
`platform/kafka/topics.yml`.
