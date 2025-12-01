"""
Complete Production Deployer with all AWS services
"""
import time
from typing import Dict, List, Optional, Any
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.table import Table

from .config import DeploymentConfig, StateManager, AWSCredentialValidator
from .managers import Route53Manager, S3Manager, ACMManager, CloudFrontManager

console = Console()


class CompleteProductionDeployer:
    """Complete production-grade website deployer with all AWS services"""
    
    def __init__(self, config: DeploymentConfig):
        self.config = config
        self.state_manager = StateManager(config.domain, config.environment)
        self.state = self.state_manager.load_state()
        
        # Get AWS account ID
        self.account_id = AWSCredentialValidator.get_account_id()
        if not self.account_id:
            raise ValueError("Could not determine AWS account ID")
        
        # Initialize all managers
        self.route53_manager = Route53Manager(config)
        self.s3_manager = S3Manager(config)
        self.acm_manager = ACMManager(config)
        self.cloudfront_manager = CloudFrontManager(config)
    
    def preflight_checks(self) -> bool:
        """Run comprehensive preflight checks"""
        console.print(Panel.fit(
            "[bold yellow]Running Preflight Checks[/bold yellow]",
            border_style="yellow"
        ))
        
        try:
            # Validate configuration
            self.config.validate()
            console.print("✅ Configuration valid")
            
            # Check AWS credentials
            if not AWSCredentialValidator.validate_credentials():
                console.print("[red]❌ AWS credentials not configured[/red]")
                return False
            console.print("✅ AWS credentials valid")
            
            # Check AWS permissions
            permissions = AWSCredentialValidator.validate_permissions(self.config)
            failed_services = [service for service, has_perm in permissions.items() if not has_perm]
            
            if failed_services:
                console.print(f"[red]❌ Missing permissions for: {', '.join(failed_services)}[/red]")
                return False
            console.print("✅ AWS permissions valid")
            
            console.print("[green]✅ All preflight checks passed[/green]")
            return True
            
        except Exception as e:
            console.print(f"[red]❌ Preflight checks failed: {e}[/red]")
            return False
    
    def deploy_phase1(self) -> Dict[str, Any]:
        """Deploy Phase 1: Route53 setup"""
        console.print(Panel.fit(
            "[bold blue]Phase 1: Route53 Hosted Zone Setup[/bold blue]",
            border_style="blue"
        ))
        
        try:
            result = self.route53_manager.create_or_get_hosted_zone()
            
            # Save state
            self.state.update({
                'hosted_zone_id': result['hosted_zone_id'],
                'ns_records': result['ns_records'],
                'phase1_complete': True
            })
            self.state_manager.save_state(self.state)
            
            # Display NS records for user verification (skip for subdomains using parent zone)
            if result.get('action') == 'existing_parent':
                console.print(f"[blue]ℹ️ Using parent domain's hosted zone: {result.get('parent_domain')}[/blue]")
                console.print("[green]✅ No NS record configuration needed for subdomain[/green]")
            else:
                self._display_ns_records(result['ns_records'])

                if result['action'] == 'created':
                    console.print("[yellow]⚠️ New hosted zone created - NS records must be configured[/yellow]")
                else:
                    console.print("[blue]ℹ️ Existing hosted zone found - verify NS records are configured[/blue]")

            return result
            
        except Exception as e:
            console.print(f"[red]❌ Phase 1 failed: {e}[/red]")
            raise
    
    def deploy_phase2(self, website_path: Optional[str] = None) -> Dict[str, Any]:
        """Deploy Phase 2: Complete deployment"""
        console.print(Panel.fit(
            "[bold blue]Phase 2: Full Website Deployment[/bold blue]",
            border_style="blue"
        ))
        
        # Check Phase 1 completion
        if not self.state.get('phase1_complete') or 'hosted_zone_id' not in self.state:
            raise ValueError("Phase 1 not completed. Run Phase 1 first.")
        
        results = {}
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            
            try:
                # Step 1: ACM Certificate
                task1 = progress.add_task("Setting up SSL certificate...", total=None)
                cert_arn = self.acm_manager.request_or_get_certificate(self.route53_manager)
                results['certificate_arn'] = cert_arn
                
                # Verify certificate is actually ready
                if not self.acm_manager.wait_for_certificate_validation(cert_arn):
                    progress.update(task1, description="❌ SSL certificate validation failed")
                    raise ValueError("SSL certificate validation failed - cannot proceed with CloudFront setup")
                
                progress.update(task1, description="✅ SSL certificate ready")
                
                # Step 2: S3 Bucket
                task2 = progress.add_task("Setting up S3 bucket...", total=None)
                bucket_name = self.s3_manager.create_or_get_bucket()
                results['bucket_name'] = bucket_name
                progress.update(task2, description="✅ S3 bucket configured")
                
                # Step 3: Upload Files
                task3 = progress.add_task("Uploading website files...", total=None)
                uploaded_count = self.s3_manager.upload_website_files(website_path)
                results['uploaded_files'] = uploaded_count
                progress.update(task3, description=f"✅ Uploaded {uploaded_count} files")
                
                # Step 4: CloudFront Distribution
                task4 = progress.add_task("Setting up CloudFront CDN...", total=None)
                distribution_info = self.cloudfront_manager.create_or_update_distribution(
                    bucket_name, cert_arn, self.account_id
                )
                results['distribution_id'] = distribution_info['id']
                results['distribution_domain'] = distribution_info['domain']
                progress.update(task4, description="✅ CloudFront distribution ready")
                
                # Step 5: Update S3 Bucket Policy
                task5 = progress.add_task("Updating S3 bucket policy...", total=None)
                self.s3_manager.update_bucket_policy(distribution_info['id'], self.account_id)
                progress.update(task5, description="✅ S3 bucket policy updated")
                
                # Step 6: Create DNS Records
                task6 = progress.add_task("Creating DNS records...", total=None)
                self.route53_manager.create_alias_records(
                    distribution_info['domain'], 
                    self.state['hosted_zone_id']
                )
                progress.update(task6, description="✅ DNS records created")
                
                # Save complete state
                self.state.update({
                    **results,
                    'phase2_complete': True,
                    'deployment_complete': True
                })
                self.state_manager.save_state(self.state)
                
                # Display appropriate success message based on domain type
                if self.config.is_subdomain:
                    success_message = (
                        f"[bold green]🎉 Subdomain Deployment Complete![/bold green]\n\n"
                        f"Subdomain URL: https://{self.config.domain}\n"
                        f"Parent Domain: {self.config.parent_domain}\n\n"
                        f"[dim]CloudFront may take 15-20 minutes to fully deploy globally[/dim]"
                    )
                else:
                    success_message = (
                        f"[bold green]🎉 Deployment Complete![/bold green]\n\n"
                        f"Website URL: https://{self.config.domain}\n"
                        f"WWW URL: https://www.{self.config.domain}\n\n"
                        f"[dim]CloudFront may take 15-20 minutes to fully deploy globally[/dim]"
                    )

                console.print(Panel.fit(
                    success_message,
                    border_style="green",
                    title="Success"
                ))
                
                return results
                
            except Exception as e:
                console.print(f"[red]❌ Phase 2 failed: {e}[/red]")
                raise
    
    def cleanup_phase1(self):
        """Cleanup Phase 1 resources (Route53)"""
        console.print(Panel.fit(
            "[bold red]Cleanup Phase 1: Route53 Resources[/bold red]",
            border_style="red"
        ))
        
        try:
            if 'hosted_zone_id' in self.state:
                self.route53_manager.delete_hosted_zone(self.state['hosted_zone_id'])
                
                # Clear Phase 1 state
                for key in ['hosted_zone_id', 'ns_records', 'phase1_complete']:
                    self.state.pop(key, None)
                
                self.state_manager.save_state(self.state)
                console.print("[green]✅ Phase 1 cleanup completed[/green]")
            else:
                console.print("[yellow]⚠️ No Phase 1 resources found[/yellow]")
                
        except Exception as e:
            console.print(f"[red]❌ Phase 1 cleanup failed: {e}[/red]")
            raise
    
    def cleanup_phase2(self):
        """Cleanup Phase 2 resources (ACM, S3, CloudFront)"""
        console.print(Panel.fit(
            "[bold red]Cleanup Phase 2: Full Deployment Resources[/bold red]",
            border_style="red"
        ))
        
        try:
            # Delete in reverse order of creation
            
            # 1. CloudFront Distribution
            if 'distribution_id' in self.state:
                console.print("[yellow]Deleting CloudFront distribution...[/yellow]")
                self.cloudfront_manager.delete_distribution(self.state['distribution_id'])
            
            # 2. S3 Bucket
            if 'bucket_name' in self.state:
                console.print("[yellow]Deleting S3 bucket and contents...[/yellow]")
                self.s3_manager.delete_bucket_and_contents()
            
            # 3. ACM Certificate
            if 'certificate_arn' in self.state:
                console.print("[yellow]Deleting ACM certificate...[/yellow]")
                self.acm_manager.delete_certificate(self.state['certificate_arn'])
            
            # Clear Phase 2 state
            phase2_keys = [
                'certificate_arn', 'bucket_name', 'distribution_id', 
                'distribution_domain', 'uploaded_files', 'phase2_complete', 
                'deployment_complete'
            ]
            for key in phase2_keys:
                self.state.pop(key, None)
            
            self.state_manager.save_state(self.state)
            console.print("[green]✅ Phase 2 cleanup completed[/green]")
            
        except Exception as e:
            console.print(f"[red]❌ Phase 2 cleanup failed: {e}[/red]")
            raise
    
    def cleanup_all(self):
        """Cleanup all resources"""
        console.print(Panel.fit(
            "[bold red]Cleanup All Resources[/bold red]",
            border_style="red"
        ))
        
        # Cleanup Phase 2 first (depends on Phase 1)
        self.cleanup_phase2()
        
        # Then cleanup Phase 1
        self.cleanup_phase1()
        
        # Remove state file
        self.state_manager.clear_state()
        console.print("[green]✅ All resources cleaned up successfully[/green]")
    
    def invalidate_cache(self, paths: List[str] = None):
        """Invalidate CloudFront cache"""
        if 'distribution_id' not in self.state:
            console.print("[red]❌ No CloudFront distribution found[/red]")
            return
        
        try:
            invalidation_id = self.cloudfront_manager.create_invalidation(
                self.state['distribution_id'], 
                paths
            )
            console.print(f"[green]✅ Cache invalidation created: {invalidation_id}[/green]")
            
        except Exception as e:
            console.print(f"[red]❌ Cache invalidation failed: {e}[/red]")
            raise
    
    def show_detailed_status(self):
        """Show detailed deployment status with resource health"""
        summary = self.state_manager.get_state_summary()
        
        console.print(f"\n[bold blue]Deployment Status: {self.config.domain}[/bold blue]")
        console.print(f"Environment: {self.config.environment}")
        console.print(f"Region: {self.config.region}")
        console.print(f"Last Updated: {summary.get('last_updated', 'Never')}")
        
        # Check resource health
        if summary['phase1_complete']:
            hz_status = self.route53_manager.get_resource_status(summary['resources']['hosted_zone_id'])
            console.print(f"✅ Route53 Hosted Zone: {hz_status}")
        
        if summary['phase2_complete']:
            if summary['resources']['certificate_arn']:
                cert_status = self.acm_manager.get_resource_status(summary['resources']['certificate_arn'])
                console.print(f"✅ ACM Certificate: {cert_status}")
            
            if summary['resources']['bucket_name']:
                bucket_status = self.s3_manager.get_resource_status()
                console.print(f"✅ S3 Bucket: {bucket_status}")
            
            if summary['resources']['distribution_id']:
                dist_status = self.cloudfront_manager.get_resource_status(summary['resources']['distribution_id'])
                console.print(f"✅ CloudFront Distribution: {dist_status}")
        
        return summary
    
    def _display_ns_records(self, ns_records: List[str]):
        """Display nameserver configuration instructions"""
        console.print("\n")
        
        table = Table(title="Nameserver Configuration Required", border_style="yellow")
        table.add_column("NS Record", style="cyan", justify="center")
        table.add_column("Value", style="white")
        
        for i, ns in enumerate(ns_records, 1):
            table.add_row(f"NS{i}", ns.rstrip('.'))
        
        console.print(table)
        
        console.print(Panel(
            f"""[bold yellow]ACTION REQUIRED:[/bold yellow]

1. Log into your domain registrar (GoDaddy, Namecheap, etc.)
2. Navigate to DNS/Nameserver settings for [bold]{self.config.domain}[/bold]
3. Change from default nameservers to custom nameservers
4. Enter the NS records shown above
5. Save changes

[italic]DNS propagation: 5-30 minutes (up to 48 hours)[/italic]""",
            title="Configure Domain Registrar",
            border_style="yellow"
        ))