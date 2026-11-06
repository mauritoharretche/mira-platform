# Public Repository Security Checklist

Before the first public commit:

- [ ] No internal PDFs, commercial dossiers, supplier lists or private roadmaps are staged.
- [ ] No credentials, tokens, keys, certificates or `.env` files are staged.
- [ ] No production data, database exports or customer records are staged.
- [ ] No personal contact details or private location details are staged unnecessarily.
- [ ] `private/` is ignored and tested with `git check-ignore`.
- [ ] `SECURITY.md` describes a private vulnerability reporting path.
- [ ] Private vulnerability reporting is enabled in GitHub settings if available.
- [ ] Dependabot alerts are enabled.
- [ ] Secret scanning and push protection are enabled where available.
- [ ] Code scanning is configured when the stack is selected.
- [ ] No license is added until maintainers decide the intended reuse rights.
- [ ] Workflow files are reviewed for permissions and log exposure.

Remember: public repository contents, commits, issues, pull requests, releases, Actions logs and artifacts may be visible to everyone.
