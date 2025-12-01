#!/bin/bash
# 🚀 Quick Deploy Script for AWSUP v2.1.0

set -e  # Exit on error

VERSION="2.1.1"
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BOLD}🚀 AWSUP v${VERSION} Deployment Script${NC}\n"

# Check if version is provided
if [ $# -eq 1 ]; then
    VERSION=$1
    echo -e "${BLUE}📝 Using custom version: ${VERSION}${NC}\n"
fi

# Step 1: Show changes
echo -e "${BOLD}📋 Files changed:${NC}"
git status --short
echo ""

# Step 2: Confirm
read -p "$(echo -e ${YELLOW}Continue with deployment? [y/N]: ${NC})" -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}❌ Deployment cancelled${NC}"
    exit 1
fi

# Step 3: Commit changes
echo -e "\n${BOLD}📝 Committing changes...${NC}"
git add .
git commit -m "feat: Add subdomain deployment support (v${VERSION})

- Auto-detect subdomains and reuse parent domain's Route53 hosted zone
- Create subdomain-specific SSL certificates (no www)
- Configure CloudFront for subdomain-only aliases
- Update documentation with subdomain examples
- Bump version to ${VERSION}
" || echo "No changes to commit"

# Step 4: Create tag
echo -e "\n${BOLD}🏷️  Creating git tag v${VERSION}...${NC}"
git tag -a "v${VERSION}" -m "Release v${VERSION} - Subdomain Support

New Features:
- Automatic subdomain detection and deployment
- Reuse parent domain Route53 hosted zone for subdomains
- Subdomain-specific SSL certificates
- No NS configuration needed for subdomains
- Cost-effective multi-subdomain deployment

Breaking Changes: None
Backward Compatible: Yes
" || echo "Tag already exists"

# Step 5: Push to GitHub
echo -e "\n${BOLD}📤 Pushing to GitHub...${NC}"
git push origin main
git push origin "v${VERSION}"

# Step 6: Wait for GitHub Actions
echo -e "\n${GREEN}✅ Git tag pushed successfully!${NC}"
echo -e "\n${BOLD}${BLUE}⏳ GitHub Actions will now:${NC}"
echo "   1. Build the package"
echo "   2. Run tests"
echo "   3. Publish to PyPI"
echo ""
echo -e "${YELLOW}📊 Monitor progress at:${NC}"
echo "   https://github.com/Akramovic1/aws-website-quick-deployer/actions"
echo ""
echo -e "${YELLOW}📦 After ~5 minutes, verify at:${NC}"
echo "   https://pypi.org/project/awsup/"
echo ""
echo -e "${GREEN}🎉 Deployment initiated successfully!${NC}"
