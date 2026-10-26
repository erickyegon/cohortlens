# Deployment

Cloud Run + Neon; dev auto-deploys on merge, prod promotes the same image digest after approval; rollback by traffic shift.

## Migration rule
Expand-then-contract: add in one release, remove in a later release, so a code rollback never meets a schema it cannot read.
