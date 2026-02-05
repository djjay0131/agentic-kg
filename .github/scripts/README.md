# Documentation Automation Scripts

This directory contains automation scripts for maintaining project documentation.

## generate_docs.py

**Purpose:** Automatically generates GitHub Pages documentation from memory-bank and construction folders.

**Triggers:**
- Automatic: When files in `memory-bank/` or `construction/` are pushed to master
- Manual: Via GitHub Actions "workflow_dispatch" trigger

**What it does:**

1. **Reads Source Files:**
   - `memory-bank/activeContext.md` - Current project state
   - `memory-bank/techContext.md` - Technical architecture
   - `memory-bank/progress.md` - Progress tracking
   - `construction/sprints/*.md` - All sprint documentation

2. **Generates Documentation:**
   - Updates `docs/index.html` with latest status
   - Creates `docs/sprints.html` with sprint overview
   - Generates `docs/architecture.html` from tech context
   - Creates `docs/progress.html` with milestones
   - Updates timestamps in `docs/SERVICE_INVENTORY.md`

3. **Deploys:**
   - Commits changes to `docs/` folder
   - Triggers GitHub Pages deployment

## Usage

### Local Testing

```bash
# From repository root
python .github/scripts/generate_docs.py

# Preview generated docs
cd docs && python -m http.server 8080
# Open http://localhost:8080
```

### GitHub Actions

The workflow runs automatically on push to master when memory-bank or construction files change.

**Manual trigger:**
```bash
# Via GitHub CLI
gh workflow run update-docs.yml

# Or via GitHub UI
# Actions → Update Documentation Pages → Run workflow
```

## Configuration

### GitHub Pages Setup

1. Go to repository Settings → Pages
2. Source: Deploy from a branch
3. Branch: `master` (or `gh-pages` if using peaceiris action)
4. Folder: `/docs`
5. Save

### Required Permissions

The workflow needs:
- `contents: write` - To commit documentation changes
- `pages: write` - To deploy to GitHub Pages
- `id-token: write` - For GitHub Pages authentication

## Extending the Agent

### Adding New Documentation Pages

Edit `generate_docs.py` and add a new method:

```python
def generate_my_page(self):
    """Generate custom documentation page."""
    html = """<!DOCTYPE html>
    <html>
    <head><title>My Page</title></head>
    <body>
        <h1>Custom Content</h1>
    </body>
    </html>
    """

    my_page = self.docs / "my-page.html"
    my_page.write_text(html, encoding='utf-8')
    print(f"✓ Generated {my_page}")
```

Then call it in the `run()` method:

```python
def run(self):
    # ... existing code ...
    self.generate_my_page()
```

### Custom Triggers

Add more paths to `.github/workflows/update-docs.yml`:

```yaml
on:
  push:
    paths:
      - 'memory-bank/**'
      - 'construction/**'
      - 'my-custom-folder/**'  # Add this
```

## Troubleshooting

### Workflow Fails

Check the Actions tab for error logs. Common issues:

- **Permission denied:** Ensure workflow has `contents: write` permission
- **Python errors:** Check that dependencies are installed
- **File not found:** Verify paths are correct relative to repo root

### Documentation Not Updating

1. Check if workflow ran: Actions → Update Documentation Pages
2. Verify changes committed: Check recent commits
3. Check GitHub Pages deployment: Settings → Pages → View deployments

### Local Script Fails

```bash
# Install dependencies
pip install markdown jinja2 pyyaml

# Run with debug output
python -v .github/scripts/generate_docs.py
```

## Architecture

```
Workflow Trigger (push/manual)
         ↓
    Checkout repo
         ↓
    Install Python & deps
         ↓
    Run generate_docs.py
         ↓
   ┌─────────────────┐
   │ Documentation   │
   │ Agent           │
   │                 │
   │ • Read sources  │
   │ • Parse sprints │
   │ • Generate HTML │
   │ • Update files  │
   └─────────────────┘
         ↓
    Check for changes
         ↓
    Commit & push (if changed)
         ↓
    Deploy to GitHub Pages
         ↓
    Documentation live! 🎉
```

## Best Practices

1. **Test locally** before pushing
2. **Keep templates simple** - Use basic HTML/CSS
3. **Use semantic versioning** for script changes
4. **Document breaking changes** in commit messages
5. **Monitor workflow runs** for failures

## Future Enhancements

- [ ] Add Markdown to HTML converter (markdown-it, mistune)
- [ ] Generate API documentation from OpenAPI specs
- [ ] Create PDF exports of documentation
- [ ] Add search functionality to docs
- [ ] Generate changelog from commits
- [ ] Add analytics tracking
- [ ] Support multiple themes
- [ ] Generate sitemap.xml
