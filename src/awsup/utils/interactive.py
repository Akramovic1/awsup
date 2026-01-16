"""
Interactive CLI for AWSUP
"""
import sys
from pathlib import Path
from typing import Optional, List
import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import DeploymentConfig, StateManager
from ..profile_manager import AWSProfileManager
from ..validators import DomainValidator

console = Console()

# Custom style for questionary
custom_style = Style([
    ('qmark', 'fg:cyan bold'),
    ('question', 'fg:white bold'),
    ('answer', 'fg:green bold'),
    ('pointer', 'fg:cyan bold'),
    ('highlighted', 'fg:cyan bold'),
    ('selected', 'fg:green'),
    ('separator', 'fg:gray'),
    ('instruction', 'fg:gray'),
])

BANNER = """
[bold cyan]
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║      █████╗ ██╗    ██╗███████╗██╗   ██╗██████╗               ║
    ║     ██╔══██╗██║    ██║██╔════╝██║   ██║██╔══██╗              ║
    ║     ███████║██║ █╗ ██║███████╗██║   ██║██████╔╝              ║
    ║     ██╔══██║██║███╗██║╚════██║██║   ██║██╔═══╝               ║
    ║     ██║  ██║╚███╔███╔╝███████║╚██████╔╝██║                   ║
    ║     ╚═╝  ╚═╝ ╚══╝╚══╝ ╚══════╝ ╚═════╝ ╚═╝                   ║
    ║                                                               ║
    ║          [white]Lightning-fast AWS Website Deployment[/white]             ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
[/bold cyan]
"""


def show_banner():
    """Display the welcome banner"""
    console.print(BANNER)
    console.print()


def get_existing_domains() -> List[str]:
    """Find existing domain configurations"""
    domains = []
    current_dir = Path('.')

    # Look for .aws-deploy-*.json files
    for config_file in current_dir.glob('.aws-deploy-*.json'):
        domain = config_file.stem.replace('.aws-deploy-', '')
        if domain:
            domains.append(domain)

    return domains


def get_domain_status(domain: str) -> dict:
    """Get deployment status for a domain"""
    try:
        state_manager = StateManager(domain)
        return state_manager.get_state_summary()
    except Exception:
        return {}


def main_menu() -> Optional[str]:
    """Display the main menu and return the selected action"""
    choices = [
        questionary.Choice('🚀 Deploy Website', value='deploy'),
        questionary.Choice('📊 Check Status', value='status'),
        questionary.Choice('🔄 Invalidate Cache', value='invalidate'),
        questionary.Choice('👤 Manage AWS Profiles', value='profiles'),
        questionary.Choice('🧹 Cleanup Resources', value='cleanup'),
        questionary.Separator(),
        questionary.Choice('❌ Exit', value='exit'),
    ]

    return questionary.select(
        'What would you like to do?',
        choices=choices,
        style=custom_style,
        use_indicator=True,
        instruction='(Use arrow keys)'
    ).ask()


def deploy_wizard(ctx) -> bool:
    """Interactive deployment wizard"""
    console.print(Panel.fit(
        "[bold cyan]🚀 Deploy Website Wizard[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    # Step 1: Get domain
    existing_domains = get_existing_domains()

    if existing_domains:
        domain_choices = [
            questionary.Choice(f'📁 {d} (existing config)', value=d)
            for d in existing_domains
        ]
        domain_choices.append(questionary.Separator())
        domain_choices.append(questionary.Choice('✨ Enter new domain', value='__new__'))

        domain = questionary.select(
            'Select domain:',
            choices=domain_choices,
            style=custom_style
        ).ask()

        if domain == '__new__':
            domain = questionary.text(
                'Enter domain name:',
                validate=lambda x: len(x) > 0 and '.' in x,
                style=custom_style
            ).ask()
    else:
        domain = questionary.text(
            'Enter domain name (e.g., example.com):',
            validate=lambda x: len(x) > 0 and '.' in x,
            style=custom_style
        ).ask()

    if not domain:
        return False

    # Normalize domain
    domain = DomainValidator.normalize_domain(domain)

    # Step 2: Select AWS profile
    profile_manager = AWSProfileManager()
    profiles = profile_manager.list_profiles()

    if profiles:
        profile_choices = [
            questionary.Choice(f'👤 {p["name"]}' + (' ✅' if p.get('valid') else ' ❌'), value=p['name'])
            for p in profiles
        ]
        profile_choices.insert(0, questionary.Choice('🔧 Use default credentials', value=None))

        aws_profile = questionary.select(
            'Select AWS profile:',
            choices=profile_choices,
            style=custom_style
        ).ask()
    else:
        aws_profile = None
        console.print("[dim]Using default AWS credentials[/dim]")

    # Step 3: Cache option
    enable_cache = questionary.confirm(
        'Enable CloudFront caching?',
        default=False,
        style=custom_style
    ).ask()

    # Step 4: Website path
    has_website = questionary.confirm(
        'Do you have website files to upload?',
        default=True,
        style=custom_style
    ).ask()

    website_path = None
    if has_website:
        website_path = questionary.path(
            'Enter path to website files:',
            default='.',
            style=custom_style
        ).ask()

    # Step 5: Confirmation
    console.print()
    console.print(Panel.fit(
        f"""[bold]Deployment Summary[/bold]

[cyan]Domain:[/cyan] {domain}
[cyan]AWS Profile:[/cyan] {aws_profile or 'default'}
[cyan]Caching:[/cyan] {'Enabled' if enable_cache else 'Disabled'}
[cyan]Website Path:[/cyan] {website_path or 'None'}""",
        title="Review",
        border_style="green"
    ))
    console.print()

    if not questionary.confirm('Proceed with deployment?', default=True, style=custom_style).ask():
        console.print("[yellow]Deployment cancelled[/yellow]")
        return False

    # Execute deployment
    console.print()
    console.print("[bold cyan]Starting deployment...[/bold cyan]")
    console.print()

    # Import here to avoid circular imports
    from ..production_deployer import CompleteProductionDeployer

    try:
        config = DeploymentConfig(domain=domain)
        config.enable_cache = enable_cache
        if aws_profile:
            config.aws_profile = aws_profile

        deployer = CompleteProductionDeployer(config)

        if not deployer.preflight_checks():
            return False

        # Phase 1
        deployer.deploy_phase1()

        console.print()
        if questionary.confirm(
            'Have you configured the NS records at your domain registrar?',
            default=False,
            style=custom_style
        ).ask():
            # Phase 2
            deployer.deploy_phase2(website_path)
            console.print()
            console.print("[bold green]✅ Deployment complete![/bold green]")
        else:
            console.print()
            console.print("[yellow]Please configure NS records, then run:[/yellow]")
            console.print(f"[cyan]  awsup phase2 {domain}[/cyan]")

        return True

    except Exception as e:
        console.print(f"[red]❌ Deployment failed: {e}[/red]")
        return False


def status_menu(ctx):
    """Interactive status check"""
    console.print(Panel.fit(
        "[bold cyan]📊 Check Deployment Status[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    existing_domains = get_existing_domains()

    if not existing_domains:
        console.print("[yellow]No domain configurations found.[/yellow]")
        console.print("[dim]Run a deployment first to create a configuration.[/dim]")
        return

    domain = questionary.select(
        'Select domain to check:',
        choices=existing_domains,
        style=custom_style
    ).ask()

    if not domain:
        return

    # Show status
    from ..production_deployer import CompleteProductionDeployer

    try:
        config = DeploymentConfig(domain=domain)
        deployer = CompleteProductionDeployer(config)
        deployer.show_detailed_status()
    except Exception as e:
        console.print(f"[red]Failed to get status: {e}[/red]")


def invalidate_menu(ctx):
    """Interactive cache invalidation"""
    console.print(Panel.fit(
        "[bold cyan]🔄 Invalidate CloudFront Cache[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    existing_domains = get_existing_domains()

    if not existing_domains:
        console.print("[yellow]No domain configurations found.[/yellow]")
        return

    domain = questionary.select(
        'Select domain:',
        choices=existing_domains,
        style=custom_style
    ).ask()

    if not domain:
        return

    # Get paths to invalidate
    invalidate_all = questionary.confirm(
        'Invalidate all paths (/*)?',
        default=True,
        style=custom_style
    ).ask()

    paths = None
    if not invalidate_all:
        paths_str = questionary.text(
            'Enter paths to invalidate (comma-separated):',
            default='/index.html',
            style=custom_style
        ).ask()
        paths = [p.strip() for p in paths_str.split(',')]

    # Execute invalidation
    from ..production_deployer import CompleteProductionDeployer

    try:
        config = DeploymentConfig(domain=domain)
        deployer = CompleteProductionDeployer(config)
        deployer.invalidate_cache(paths)
        console.print("[green]✅ Cache invalidation created[/green]")
    except Exception as e:
        console.print(f"[red]Failed to invalidate cache: {e}[/red]")


def profiles_menu(ctx):
    """Interactive AWS profiles management"""
    while True:
        console.print(Panel.fit(
            "[bold cyan]👤 Manage AWS Profiles[/bold cyan]",
            border_style="cyan"
        ))
        console.print()

        action = questionary.select(
            'What would you like to do?',
            choices=[
                questionary.Choice('📋 List profiles', value='list'),
                questionary.Choice('➕ Add profile', value='add'),
                questionary.Choice('➖ Remove profile', value='remove'),
                questionary.Separator(),
                questionary.Choice('⬅️  Back to main menu', value='back'),
            ],
            style=custom_style
        ).ask()

        if action == 'back' or action is None:
            break

        if action == 'list':
            list_profiles()
        elif action == 'add':
            add_profile()
        elif action == 'remove':
            remove_profile()

        console.print()


def list_profiles():
    """List all AWS profiles"""
    manager = AWSProfileManager()
    profiles = manager.list_profiles()

    if not profiles:
        console.print("[yellow]No AWS profiles found[/yellow]")
        console.print("[dim]Run 'Add profile' to create one[/dim]")
        return

    table = Table(title="AWS Profiles", border_style="blue")
    table.add_column("Profile Name", style="cyan")
    table.add_column("Account ID", style="white")
    table.add_column("Status", style="white")

    for p in profiles:
        status = "[green]Valid[/green]" if p.get('valid') else "[red]Invalid[/red]"
        account = p.get('account_id') or 'N/A'
        table.add_row(p['name'], account, status)

    console.print(table)


def add_profile():
    """Add a new AWS profile"""
    name = questionary.text(
        'Profile name:',
        validate=lambda x: len(x) > 0,
        style=custom_style
    ).ask()

    if not name:
        return

    access_key = questionary.text(
        'AWS Access Key ID:',
        validate=lambda x: len(x) > 0,
        style=custom_style
    ).ask()

    if not access_key:
        return

    secret_key = questionary.password(
        'AWS Secret Access Key:',
        validate=lambda x: len(x) > 0,
        style=custom_style
    ).ask()

    if not secret_key:
        return

    region = questionary.text(
        'Default region (optional):',
        default='',
        style=custom_style
    ).ask()

    manager = AWSProfileManager()
    success, message = manager.add_profile(
        name,
        access_key,
        secret_key,
        region if region else None
    )

    if success:
        console.print(f"[green]✅ {message}[/green]")
    else:
        console.print(f"[red]❌ {message}[/red]")


def remove_profile():
    """Remove an AWS profile"""
    manager = AWSProfileManager()
    profiles = manager.list_profiles()

    if not profiles:
        console.print("[yellow]No profiles to remove[/yellow]")
        return

    profile_names = [p['name'] for p in profiles if p['name'] != 'default']

    if not profile_names:
        console.print("[yellow]No removable profiles (cannot remove 'default')[/yellow]")
        return

    name = questionary.select(
        'Select profile to remove:',
        choices=profile_names,
        style=custom_style
    ).ask()

    if not name:
        return

    if questionary.confirm(
        f'Are you sure you want to remove profile "{name}"?',
        default=False,
        style=custom_style
    ).ask():
        success, message = manager.remove_profile(name)
        if success:
            console.print(f"[green]✅ {message}[/green]")
        else:
            console.print(f"[red]❌ {message}[/red]")


def cleanup_menu(ctx):
    """Interactive cleanup"""
    console.print(Panel.fit(
        "[bold red]🧹 Cleanup AWS Resources[/bold red]",
        border_style="red"
    ))
    console.print()

    existing_domains = get_existing_domains()

    if not existing_domains:
        console.print("[yellow]No domain configurations found.[/yellow]")
        return

    domain = questionary.select(
        'Select domain to cleanup:',
        choices=existing_domains,
        style=custom_style
    ).ask()

    if not domain:
        return

    phase = questionary.select(
        'What would you like to cleanup?',
        choices=[
            questionary.Choice('Phase 1 only (Route53)', value='1'),
            questionary.Choice('Phase 2 only (S3, CloudFront, ACM)', value='2'),
            questionary.Choice('Everything (all resources)', value='all'),
            questionary.Separator(),
            questionary.Choice('Cancel', value='cancel'),
        ],
        style=custom_style
    ).ask()

    if phase == 'cancel' or phase is None:
        return

    console.print()
    console.print("[bold red]⚠️  WARNING: This will delete AWS resources![/bold red]")

    if not questionary.confirm(
        'Are you absolutely sure?',
        default=False,
        style=custom_style
    ).ask():
        console.print("[yellow]Cleanup cancelled[/yellow]")
        return

    # Execute cleanup
    from ..production_deployer import CompleteProductionDeployer

    try:
        config = DeploymentConfig(domain=domain)
        deployer = CompleteProductionDeployer(config)

        if phase == '1':
            deployer.cleanup_phase1()
        elif phase == '2':
            deployer.cleanup_phase2()
        else:
            deployer.cleanup_all()

        console.print("[green]✅ Cleanup complete[/green]")
    except Exception as e:
        console.print(f"[red]❌ Cleanup failed: {e}[/red]")


def launch_interactive(ctx):
    """Main entry point for interactive mode"""
    show_banner()

    while True:
        action = main_menu()

        if action is None or action == 'exit':
            console.print()
            console.print("[cyan]Goodbye! 👋[/cyan]")
            sys.exit(0)

        console.print()

        if action == 'deploy':
            deploy_wizard(ctx)
        elif action == 'status':
            status_menu(ctx)
        elif action == 'invalidate':
            invalidate_menu(ctx)
        elif action == 'profiles':
            profiles_menu(ctx)
        elif action == 'cleanup':
            cleanup_menu(ctx)

        console.print()
        questionary.press_any_key_to_continue(
            'Press any key to continue...',
            style=custom_style
        ).ask()
        console.clear()
        show_banner()
