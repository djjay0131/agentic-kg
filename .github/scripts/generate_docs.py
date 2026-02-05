#!/usr/bin/env python3
"""
Documentation Generator Agent

Automatically generates GitHub Pages documentation from memory-bank and construction folders.
Triggered by GitHub Actions on sprint completion or manual trigger.
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
import yaml


class DocumentationAgent:
    """Agent that generates documentation from project files."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)
        self.memory_bank = self.repo_root / "memory-bank"
        self.construction = self.repo_root / "construction"
        self.docs = self.repo_root / "docs"

    def run(self):
        """Main execution flow."""
        print("🤖 Documentation Agent starting...")

        # Read source files
        active_context = self.read_markdown(self.memory_bank / "activeContext.md")
        tech_context = self.read_markdown(self.memory_bank / "techContext.md")
        progress = self.read_markdown(self.memory_bank / "progress.md")

        # Parse sprint files
        sprints = self.parse_sprints()

        # Generate documentation pages
        self.generate_index(active_context, progress)
        self.generate_sprints_page(sprints)
        self.generate_architecture_page(tech_context)
        self.generate_progress_page(progress)

        # Update service inventory
        self.update_service_inventory()

        print("✅ Documentation generation complete!")

    def read_markdown(self, filepath: Path) -> str:
        """Read markdown file content."""
        if filepath.exists():
            return filepath.read_text(encoding='utf-8')
        return ""

    def parse_sprints(self) -> List[Dict[str, Any]]:
        """Parse all sprint files."""
        sprints_dir = self.construction / "sprints"
        sprints = []

        if not sprints_dir.exists():
            return sprints

        for sprint_file in sorted(sprints_dir.glob("sprint-*.md")):
            content = self.read_markdown(sprint_file)

            # Extract sprint metadata
            sprint_match = re.search(r'# Sprint (\d+): (.+)', content)
            status_match = re.search(r'\*\*Status:\*\* (.+)', content)
            date_match = re.search(r'\*\*Start Date:\*\* (.+)', content)

            if sprint_match:
                sprint_num = sprint_match.group(1)
                sprint_name = sprint_match.group(2)
                status = status_match.group(1) if status_match else "Unknown"
                start_date = date_match.group(1) if date_match else "N/A"

                sprints.append({
                    'number': sprint_num,
                    'name': sprint_name,
                    'status': status,
                    'start_date': start_date,
                    'filename': sprint_file.name,
                    'content': content
                })

        return sprints

    def generate_index(self, active_context: str, progress: str):
        """Generate enhanced index.html with dynamic content."""

        # Extract current phase
        phase_match = re.search(r'\*\*Phase \d+: (.+?)\*\*', active_context)
        current_phase = phase_match.group(1) if phase_match else "Active Development"

        # Extract test status
        test_match = re.search(r'Unit Tests: (\d+) passed, (\d+) failed', active_context)
        tests_passed = test_match.group(1) if test_match else "?"
        tests_failed = test_match.group(2) if test_match else "?"

        # Count completed sprints
        completed_sprints = len(re.findall(r'✅', progress))

        # Update timestamp
        last_updated = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

        # Generate status badges HTML
        status_html = f"""
        <div class="info-box">
            <h4>📊 Project Status</h4>
            <p><strong>Current Phase:</strong> {current_phase}</p>
            <p><strong>Completed Sprints:</strong> {completed_sprints}</p>
            <p><strong>Test Status:</strong> {tests_passed} passed, {tests_failed} failed</p>
            <p><strong>Last Updated:</strong> {last_updated}</p>
        </div>
        """

        # Read existing index.html
        index_path = self.docs / "index.html"
        if index_path.exists():
            content = index_path.read_text(encoding='utf-8')

            # Update status section if it exists
            if '<!-- AUTO_GENERATED_STATUS -->' in content:
                content = re.sub(
                    r'<!-- AUTO_GENERATED_STATUS -->.*?<!-- /AUTO_GENERATED_STATUS -->',
                    f'<!-- AUTO_GENERATED_STATUS -->\n{status_html}\n<!-- /AUTO_GENERATED_STATUS -->',
                    content,
                    flags=re.DOTALL
                )
            else:
                # Insert before footer
                content = content.replace(
                    '<footer>',
                    f'<!-- AUTO_GENERATED_STATUS -->\n{status_html}\n<!-- /AUTO_GENERATED_STATUS -->\n\n<footer>'
                )

            # Update timestamp in footer
            content = re.sub(
                r'Last Updated: \d{4}-\d{2}-\d{2}',
                f'Last Updated: {datetime.now().strftime("%Y-%m-%d")}',
                content
            )

            index_path.write_text(content, encoding='utf-8')
            print(f"✓ Updated {index_path}")

    def generate_sprints_page(self, sprints: List[Dict[str, Any]]):
        """Generate sprints overview page."""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sprint Overview - Agentic KG</title>
    <link rel="stylesheet" href="styles.css">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }
        .sprint-card {
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .sprint-card h3 {
            color: #667eea;
            margin-top: 0;
        }
        .status-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
        }
        .status-complete { background: #d4edda; color: #155724; }
        .status-planning { background: #fff3cd; color: #856404; }
        .status-active { background: #cfe2ff; color: #084298; }
    </style>
</head>
<body>
    <h1>📋 Sprint Overview</h1>
    <p><a href="index.html">← Back to Home</a></p>
"""

        for sprint in sprints:
            status_class = 'status-complete' if 'Complete' in sprint['status'] else 'status-planning'
            html += f"""
    <div class="sprint-card">
        <h3>Sprint {sprint['number']}: {sprint['name']}</h3>
        <p>
            <span class="status-badge {status_class}">{sprint['status']}</span>
            <span style="margin-left: 20px;">Started: {sprint['start_date']}</span>
        </p>
        <p><a href="https://github.com/djjay0131/agentic-kg/blob/master/construction/sprints/{sprint['filename']}" target="_blank">View Details →</a></p>
    </div>
"""

        html += """
</body>
</html>
"""

        sprints_page = self.docs / "sprints.html"
        sprints_page.write_text(html, encoding='utf-8')
        print(f"✓ Generated {sprints_page}")

    def generate_architecture_page(self, tech_context: str):
        """Generate architecture documentation page."""
        # Convert markdown to HTML (simplified)
        html_content = tech_context.replace('\n## ', '\n<h2>').replace('\n### ', '\n<h3>')
        html_content = re.sub(r'```(\w+)\n(.*?)\n```', r'<pre><code class="\1">\2</code></pre>', html_content, flags=re.DOTALL)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Architecture - Agentic KG</title>
</head>
<body>
    <h1>🏗️ Architecture</h1>
    <p><a href="index.html">← Back to Home</a></p>
    <div class="content">
        {html_content}
    </div>
</body>
</html>
"""

        arch_page = self.docs / "architecture.html"
        arch_page.write_text(html, encoding='utf-8')
        print(f"✓ Generated {arch_page}")

    def generate_progress_page(self, progress: str):
        """Generate progress tracking page."""
        # Extract milestones
        milestones = re.findall(r'### (M\d+): (.+?) (✅|❌)', progress)

        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Progress - Agentic KG</title>
</head>
<body>
    <h1>📈 Progress Tracking</h1>
    <p><a href="index.html">← Back to Home</a></p>
    <h2>Milestones</h2>
"""

        for milestone, name, status in milestones:
            icon = "✅" if status == "✅" else "⏳"
            html += f"<p>{icon} <strong>{milestone}:</strong> {name}</p>\n"

        html += """
</body>
</html>
"""

        progress_page = self.docs / "progress.html"
        progress_page.write_text(html, encoding='utf-8')
        print(f"✓ Generated {progress_page}")

    def update_service_inventory(self):
        """Update SERVICE_INVENTORY.md timestamp."""
        inventory_path = self.docs / "SERVICE_INVENTORY.md"
        if inventory_path.exists():
            content = inventory_path.read_text(encoding='utf-8')
            content = re.sub(
                r'\*\*Last Updated:\*\* \d{4}-\d{2}-\d{2}',
                f'**Last Updated:** {datetime.now().strftime("%Y-%m-%d")}',
                content
            )
            inventory_path.write_text(content, encoding='utf-8')
            print(f"✓ Updated {inventory_path}")


def main():
    """Main entry point."""
    repo_root = Path(os.environ.get('GITHUB_WORKSPACE', '.')).absolute()
    agent = DocumentationAgent(repo_root)
    agent.run()


if __name__ == '__main__':
    main()
