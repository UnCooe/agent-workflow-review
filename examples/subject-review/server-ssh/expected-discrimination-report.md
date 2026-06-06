# Expected server-ssh discrimination behavior

- Git SSH fixture: rejected or review-only because negative Git-domain signals match.
- Server SSH fixture: likely or confirmed because server domain anchors match.
- Mixed fixture: collision/review-only is acceptable if Git and server anchors overlap.
- No raw host, user, path, or prompt text should appear in output artifacts.
