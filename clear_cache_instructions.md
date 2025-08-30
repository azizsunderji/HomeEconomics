# How to Clear Server-Side Cache for home-economics.us

The new files are uploaded to the server but being cached somewhere. Here are the steps to clear each potential cache layer:

## 1. WordPress Caching Plugins
Check your WordPress admin for these common caching plugins:
- **W3 Total Cache**: Dashboard → Performance → Dashboard → "Empty all caches"
- **WP Rocket**: Settings → WP Rocket → Dashboard → "Clear Cache"
- **WP Super Cache**: Settings → WP Super Cache → "Delete Cache"
- **LiteSpeed Cache**: LiteSpeed Cache → Toolbox → "Purge All"

## 2. Cloudflare (if you're using it)
1. Log into Cloudflare dashboard
2. Select your domain
3. Go to Caching → Configuration
4. Click "Purge Everything" or
5. Custom Purge: Enter these URLs:
   - https://www.home-economics.us/live/rankings/*
   - https://home-economics.us/live/rankings/*

## 3. Server-Level Cache (via hosting provider)
- **SiteGround**: Site Tools → Speed → Caching → Dynamic Cache → Flush Cache
- **WP Engine**: WP Engine dashboard → Cache → Clear all caches
- **Kinsta**: MyKinsta → Sites → Tools → Clear cache
- **Bluehost**: My Sites → Performance → Clear Cache

## 4. Direct File System Check (via FTP/SSH)
1. Connect via FTP/SSH to your server
2. Navigate to: `/wp-content/uploads/reports/live/rankings/`
3. Check the file timestamps - they should show today's date
4. If old, the GitHub Action deployment might be failing

## 5. Temporary Workaround - Rename Files
As a quick test, we could:
1. Deploy files with new names (e.g., `median_sale_price_v2.html`)
2. Update links to point to new names
3. This would bypass any file-level caching

## 6. Check .htaccess is Working
The .htaccess file we created might not be taking effect. Try:
1. Access: https://www.home-economics.us/live/rankings/.htaccess
2. If you get 403 Forbidden - good, it's there
3. If you get 404 - the file wasn't uploaded

## Which Cache is Most Likely?
Given that:
- You can download the new files directly from the server
- But the website shows old content
- Even with ?v=new parameter

This suggests **WordPress-level caching** (plugin or hosting provider cache) rather than browser or CDN caching.

## Immediate Action:
1. Check WordPress admin for caching plugins
2. Clear all caches you find
3. Check with your hosting provider for server-level cache options
4. Wait 2-3 minutes and test again

Let me know which caching system you find, and I can help with specific instructions!