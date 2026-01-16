#!/usr/bin/env python3
"""
AWS Website Deployer CLI Entry Point
"""
import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
import click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.table import Table

from .config import DeploymentConfig, AWSCredentialValidator
from .validators import DomainValidator, FileValidator, SecurityValidator
from .production_deployer import CompleteProductionDeployer
from .profile_manager import AWSProfileManager

console = Console()

# Configure logging with rich
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, rich_tracebacks=True)]
)
logger = logging.getLogger(__name__)


class ProductionDeployer:
    """Legacy wrapper for backward compatibility"""
    
    def __init__(self, config: DeploymentConfig):
        self.deployer = CompleteProductionDeployer(config)
    
    def preflight_checks(self) -> bool:
        """Run comprehensive preflight checks"""
        return self.deployer.preflight_checks()
    
    def deploy_phase1(self) -> Dict:
        """Deploy Phase 1: Route53 setup"""
        return self.deployer.deploy_phase1()
    
    def deploy_phase2(self, website_path: Optional[str] = None) -> Dict:
        """Deploy Phase 2: Full deployment"""
        return self.deployer.deploy_phase2(website_path)
    
    def show_deployment_status(self):
        """Display current deployment status"""
        return self.deployer.show_detailed_status()


@click.group(invoke_without_command=True)
@click.option('--config', '-c', help='Configuration file path')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
@click.option('--profile', '-p', help='AWS profile to use')
@click.pass_context
def cli(ctx, config, verbose, profile):
    """Production-grade AWS website deployment tool"""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config
    ctx.obj['aws_profile'] = profile

    # Launch interactive mode if no command provided
    if ctx.invoked_subcommand is None:
        from .utils.interactive import launch_interactive
        launch_interactive(ctx)


@cli.command()
@click.argument('domain')
@click.option('--region', default='us-east-1', help='AWS region')
@click.option('--environment', default='prod', help='Environment name')
@click.pass_context
def init(ctx, domain, region, environment):
    """Initialize deployment configuration"""
    try:
        # Validate domain
        is_valid, error = DomainValidator.validate_domain(domain)
        if not is_valid:
            console.print(f"[red]❌ {error}[/red]")
            sys.exit(1)
        
        # Create configuration
        config = DeploymentConfig(
            domain=DomainValidator.normalize_domain(domain),
            region=region,
            environment=environment
        )
        
        # Save configuration
        config_path = ctx.obj.get('config_path') or f'.aws-deploy-{domain}.json'
        config.to_file(config_path)
        
        console.print(f"[green]Configuration saved to {config_path}[/green]")
        console.print(f"[blue]Domain: {config.domain}[/blue]")
        console.print(f"[blue]Region: {config.region}[/blue]")
        console.print(f"[blue]Environment: {config.environment}[/blue]")
        
    except Exception as e:
        console.print(f"[red]Initialization failed: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('domain')
@click.pass_context
def phase1(ctx, domain):
    """Deploy Phase 1: Route53 setup"""
    try:
        config = _load_config(ctx, domain)
        deployer = ProductionDeployer(config)
        
        if not deployer.preflight_checks():
            sys.exit(1)
        
        deployer.deploy_phase1()
        
    except Exception as e:
        console.print(f"[red]❌ Phase 1 deployment failed: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('domain')
@click.option('--website-path', help='Path to website files')
@click.option('--cache/--no-cache', default=None, help='Enable/disable CloudFront caching')
@click.pass_context
def phase2(ctx, domain, website_path, cache):
    """Deploy Phase 2: Full deployment"""
    try:
        config = _load_config(ctx, domain)

        # Handle cache setting - prompt if not specified
        if cache is None:
            cache = click.confirm(
                "Enable CloudFront caching?",
                default=False
            )

        config.enable_cache = cache

        deployer = ProductionDeployer(config)

        if not deployer.preflight_checks():
            sys.exit(1)

        # Validate website path if provided
        if website_path:
            is_valid, error = FileValidator.validate_website_path(website_path)
            if not is_valid:
                console.print(f"[red]❌ Website validation failed: {error}[/red]")
                sys.exit(1)
        
        deployer.deploy_phase2(website_path)
        
    except Exception as e:
        console.print(f"[red]❌ Phase 2 deployment failed: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('domain')
@click.option('--website-path', help='Path to website files')
@click.option('--cache/--no-cache', default=None, help='Enable/disable CloudFront caching')
@click.pass_context
def deploy(ctx, domain, website_path, cache):
    """Deploy both phases (complete deployment)"""
    try:
        config = _load_config(ctx, domain)

        # Handle cache setting - prompt if not specified
        if cache is None:
            cache = click.confirm(
                "Enable CloudFront caching?",
                default=False
            )

        config.enable_cache = cache

        deployer = ProductionDeployer(config)

        if not deployer.preflight_checks():
            sys.exit(1)

        # Phase 1
        result = deployer.deploy_phase1()
        
        # Always ask about NS record configuration
        console.print("\n")
        response = click.confirm("Have you configured the NS records at your domain registrar?")
        if not response:
            console.print("[blue]Please configure the NS records shown above at your registrar, then run:[/blue]")
            console.print(f"[blue]   awsup phase2 {domain}[/blue]")
            if website_path:
                console.print(f"[blue]   --website-path {website_path}[/blue]")
            sys.exit(0)
        
        # Phase 2
        deployer.deploy_phase2(website_path)
        
    except Exception as e:
        console.print(f"[red]❌ Complete deployment failed: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('domain')
@click.pass_context
def status(ctx, domain):
    """Show deployment status"""
    try:
        config = _load_config(ctx, domain)
        deployer = ProductionDeployer(config)
        deployer.show_deployment_status()
        
    except Exception as e:
        console.print(f"[red]❌ Failed to get status: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('domain')
@click.option('--paths', help='Comma-separated paths to invalidate (default: /*)')
@click.pass_context
def invalidate(ctx, domain, paths):
    """Invalidate CloudFront cache"""
    try:
        config = _load_config(ctx, domain)
        deployer = ProductionDeployer(config)
        
        path_list = paths.split(',') if paths else None
        deployer.deployer.invalidate_cache(path_list)
        
    except Exception as e:
        console.print(f"[red]❌ Cache invalidation failed: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('domain')
@click.option('--phase', type=click.Choice(['1', '2', 'all']), default='all', help='Which phase to cleanup')
@click.confirmation_option(prompt='This will delete AWS resources. Continue?')
@click.pass_context
def cleanup(ctx, domain, phase):
    """Cleanup AWS resources"""
    try:
        config = _load_config(ctx, domain)
        deployer = ProductionDeployer(config)
        
        if phase == '1':
            deployer.deployer.cleanup_phase1()
        elif phase == '2':
            deployer.deployer.cleanup_phase2()
        else:  # all
            deployer.deployer.cleanup_all()
        
    except Exception as e:
        console.print(f"[red]❌ Cleanup failed: {e}[/red]")
        sys.exit(1)


def _load_config(ctx, domain: str) -> DeploymentConfig:
    """Load configuration from file or create default"""
    config_path = ctx.obj.get('config_path') or f'.aws-deploy-{domain}.json'
    aws_profile = ctx.obj.get('aws_profile')

    try:
        if Path(config_path).exists():
            config = DeploymentConfig.from_file(config_path)
        else:
            # Create default config
            config = DeploymentConfig(domain=domain)

        # Override profile if provided via CLI
        if aws_profile:
            config.aws_profile = aws_profile

        return config
    except Exception as e:
        console.print(f"[red]❌ Failed to load configuration: {e}[/red]")
        sys.exit(1)


@cli.group()
def profile():
    """Manage AWS profiles"""
    pass


@profile.command('list')
def profile_list():
    """List all AWS profiles"""
    try:
        manager = AWSProfileManager()
        profiles = manager.list_profiles()

        if not profiles:
            console.print("[yellow]No AWS profiles found[/yellow]")
            console.print("[dim]Run 'awsup profile add <name>' to create one[/dim]")
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

    except Exception as e:
        console.print(f"[red]Failed to list profiles: {e}[/red]")
        sys.exit(1)


@profile.command('add')
@click.argument('name')
@click.option('--access-key', prompt='AWS Access Key ID', help='AWS Access Key ID')
@click.option('--secret-key', prompt='AWS Secret Access Key', hide_input=True,
              help='AWS Secret Access Key')
@click.option('--region', default=None, help='Default region for this profile')
def profile_add(name, access_key, secret_key, region):
    """Add a new AWS profile"""
    try:
        manager = AWSProfileManager()

        # Validate credentials before saving
        console.print("[blue]Validating credentials...[/blue]")

        import boto3
        try:
            session = boto3.Session(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region or 'us-east-1'
            )
            sts = session.client('sts')
            identity = sts.get_caller_identity()
            console.print(f"[green]Credentials valid for account: {identity['Account']}[/green]")
        except Exception as e:
            console.print(f"[red]Invalid credentials: {e}[/red]")
            if not click.confirm("Save anyway?", default=False):
                sys.exit(1)

        success, message = manager.add_profile(name, access_key, secret_key, region)

        if success:
            console.print(f"[green]Profile '{name}' added successfully[/green]")
        else:
            console.print(f"[red]{message}[/red]")
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]Failed to add profile: {e}[/red]")
        sys.exit(1)


@profile.command('remove')
@click.argument('name')
@click.confirmation_option(prompt='This will remove the profile. Continue?')
def profile_remove(name):
    """Remove an AWS profile"""
    try:
        manager = AWSProfileManager()
        success, message = manager.remove_profile(name)

        if success:
            console.print(f"[green]{message}[/green]")
        else:
            console.print(f"[red]{message}[/red]")
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]Failed to remove profile: {e}[/red]")
        sys.exit(1)


@profile.command('select')
@click.argument('name')
@click.argument('domain')
@click.pass_context
def profile_select(ctx, name, domain):
    """Select AWS profile for a domain's deployments"""
    try:
        # Validate profile exists and works
        manager = AWSProfileManager()
        valid, info = manager.validate_profile(name)

        if not valid:
            console.print(f"[red]Profile '{name}' is invalid or credentials don't work[/red]")
            sys.exit(1)

        # Load and update config
        config_path = ctx.obj.get('config_path') or f'.aws-deploy-{domain}.json'

        if Path(config_path).exists():
            config = DeploymentConfig.from_file(config_path)
        else:
            config = DeploymentConfig(domain=domain)

        config.aws_profile = name

        # Save updated config
        config.to_file(config_path)

        console.print(f"[green]Profile '{name}' selected for domain '{domain}'[/green]")
        console.print(f"[blue]Account: {info['account_id']}[/blue]")

    except Exception as e:
        console.print(f"[red]Failed to select profile: {e}[/red]")
        sys.exit(1)


def main():
    """Main entry point for the CLI"""
    cli()


if __name__ == '__main__':
    main()