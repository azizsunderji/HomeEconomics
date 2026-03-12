<?php
/**
 * Social media posting endpoint for Home Economics Charts.
 * Accepts image + text, posts to Twitter or Bluesky with the image attached.
 *
 * POST /api/post-social.php
 * Body: multipart/form-data with fields:
 *   - platform: "twitter" | "bluesky"
 *   - text: post text
 *   - image: PNG file upload
 *
 * Returns JSON: { "success": true, "url": "https://..." }
 *          or:  { "success": false, "error": "..." }
 */

header('Content-Type: application/json');

// Only allow POST
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['success' => false, 'error' => 'Method not allowed']);
    exit;
}

// Load config
$configPath = __DIR__ . '/config.php';
if (!file_exists($configPath)) {
    http_response_code(500);
    echo json_encode(['success' => false, 'error' => 'Server not configured — config.php missing']);
    exit;
}
$config = require $configPath;

$platform = $_POST['platform'] ?? '';
$text = $_POST['text'] ?? '';

if (!in_array($platform, ['twitter', 'bluesky'])) {
    echo json_encode(['success' => false, 'error' => 'Invalid platform']);
    exit;
}

if (empty($text)) {
    echo json_encode(['success' => false, 'error' => 'Text is required']);
    exit;
}

if (!isset($_FILES['image']) || $_FILES['image']['error'] !== UPLOAD_ERR_OK) {
    echo json_encode(['success' => false, 'error' => 'Image upload failed']);
    exit;
}

$imageData = file_get_contents($_FILES['image']['tmp_name']);
$imageMime = $_FILES['image']['type'] ?: 'image/png';

// ─── Bluesky ────────────────────────────────────────────────────────────────

if ($platform === 'bluesky') {
    $bsky = $config['bluesky'] ?? [];
    if (empty($bsky['handle']) || empty($bsky['password'])) {
        echo json_encode(['success' => false, 'error' => 'Bluesky credentials not configured']);
        exit;
    }

    // 1. Create session
    $session = bsky_request('POST', 'com.atproto.server.createSession', [
        'identifier' => $bsky['handle'],
        'password'   => $bsky['password'],
    ]);

    if (!isset($session['did'])) {
        echo json_encode(['success' => false, 'error' => 'Bluesky auth failed: ' . json_encode($session)]);
        exit;
    }

    $did = $session['did'];
    $accessJwt = $session['accessJwt'];

    // 2. Upload image blob
    $blobResp = bsky_upload($accessJwt, $imageData, $imageMime);
    if (!isset($blobResp['blob'])) {
        echo json_encode(['success' => false, 'error' => 'Bluesky image upload failed: ' . json_encode($blobResp)]);
        exit;
    }

    // 3. Create post with image embed
    $now = gmdate('Y-m-d\TH:i:s\Z');
    $record = [
        'repo'       => $did,
        'collection' => 'app.bsky.feed.post',
        'record'     => [
            '$type'   => 'app.bsky.feed.post',
            'text'    => $text,
            'createdAt' => $now,
            'embed'   => [
                '$type'  => 'app.bsky.embed.images',
                'images' => [
                    [
                        'alt'   => 'Home Economics chart',
                        'image' => $blobResp['blob'],
                    ],
                ],
            ],
        ],
    ];

    $postResp = bsky_request('POST', 'com.atproto.repo.createRecord', $record, $accessJwt);
    if (!isset($postResp['uri'])) {
        echo json_encode(['success' => false, 'error' => 'Bluesky post failed: ' . json_encode($postResp)]);
        exit;
    }

    // Convert AT URI to web URL
    // uri format: at://did:plc:xxx/app.bsky.feed.post/rkey
    $parts = explode('/', $postResp['uri']);
    $rkey = end($parts);
    $postUrl = "https://bsky.app/profile/{$bsky['handle']}/post/{$rkey}";

    echo json_encode(['success' => true, 'url' => $postUrl]);
    exit;
}

// ─── Twitter ────────────────────────────────────────────────────────────────

if ($platform === 'twitter') {
    $tw = $config['twitter'] ?? [];
    if (empty($tw['api_key']) || empty($tw['api_secret']) || empty($tw['access_token']) || empty($tw['access_token_secret'])) {
        echo json_encode(['success' => false, 'error' => 'Twitter credentials not configured']);
        exit;
    }

    // 1. Upload media via v1.1 chunked upload
    $mediaId = twitter_upload_media($tw, $imageData, $imageMime);
    if (!$mediaId) {
        echo json_encode(['success' => false, 'error' => 'Twitter media upload failed']);
        exit;
    }

    // 2. Create tweet with media via v2
    $tweetResp = twitter_request($tw, 'POST', 'https://api.twitter.com/2/tweets', [
        'text'  => $text,
        'media' => ['media_ids' => [$mediaId]],
    ]);

    if (!isset($tweetResp['data']['id'])) {
        echo json_encode(['success' => false, 'error' => 'Twitter post failed: ' . json_encode($tweetResp)]);
        exit;
    }

    $tweetId = $tweetResp['data']['id'];
    // Get username from config or default
    $username = $tw['username'] ?? 'AzizSunderji';
    $postUrl = "https://twitter.com/{$username}/status/{$tweetId}";

    echo json_encode(['success' => true, 'url' => $postUrl]);
    exit;
}

// ─── Bluesky helpers ────────────────────────────────────────────────────────

function bsky_request($method, $endpoint, $body = null, $token = null) {
    $url = "https://bsky.social/xrpc/{$endpoint}";
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 30);

    $headers = ['Content-Type: application/json'];
    if ($token) {
        $headers[] = "Authorization: Bearer {$token}";
    }

    if ($method === 'POST') {
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
    }

    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    $resp = curl_exec($ch);
    curl_close($ch);
    return json_decode($resp, true) ?: [];
}

function bsky_upload($token, $imageData, $mime) {
    $url = "https://bsky.social/xrpc/com.atproto.repo.uploadBlob";
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 30);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $imageData);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        "Authorization: Bearer {$token}",
        "Content-Type: {$mime}",
    ]);
    $resp = curl_exec($ch);
    curl_close($ch);
    return json_decode($resp, true) ?: [];
}

// ─── Twitter helpers (OAuth 1.0a) ───────────────────────────────────────────

function twitter_upload_media($tw, $imageData, $mime) {
    // Simple (non-chunked) media upload for images < 5MB
    $url = 'https://upload.twitter.com/1.1/media/upload.json';
    $base64 = base64_encode($imageData);

    $params = ['media_data' => $base64];
    $authHeader = twitter_oauth_header($tw, 'POST', $url, $params);

    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 30);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($params));
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        "Authorization: {$authHeader}",
    ]);
    $resp = curl_exec($ch);
    curl_close($ch);

    $data = json_decode($resp, true);
    return $data['media_id_string'] ?? null;
}

function twitter_request($tw, $method, $url, $jsonBody) {
    // For v2 endpoints, OAuth 1.0a signature uses the base URL without body params
    $authHeader = twitter_oauth_header($tw, $method, $url, []);

    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 30);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($jsonBody));
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        "Authorization: {$authHeader}",
        'Content-Type: application/json',
    ]);
    $resp = curl_exec($ch);
    curl_close($ch);

    return json_decode($resp, true) ?: [];
}

function twitter_oauth_header($tw, $method, $url, $extraParams = []) {
    $oauthParams = [
        'oauth_consumer_key'     => $tw['api_key'],
        'oauth_nonce'            => bin2hex(random_bytes(16)),
        'oauth_signature_method' => 'HMAC-SHA1',
        'oauth_timestamp'        => (string)time(),
        'oauth_token'            => $tw['access_token'],
        'oauth_version'          => '1.0',
    ];

    // Signature base: all oauth params + extra params (for form-encoded endpoints)
    $sigParams = array_merge($oauthParams, $extraParams);
    ksort($sigParams);

    $paramString = http_build_query($sigParams, '', '&', PHP_QUERY_RFC3986);
    $baseString = strtoupper($method) . '&' . rawurlencode($url) . '&' . rawurlencode($paramString);
    $signingKey = rawurlencode($tw['api_secret']) . '&' . rawurlencode($tw['access_token_secret']);

    $oauthParams['oauth_signature'] = base64_encode(hash_hmac('sha1', $baseString, $signingKey, true));

    $parts = [];
    foreach ($oauthParams as $k => $v) {
        $parts[] = rawurlencode($k) . '="' . rawurlencode($v) . '"';
    }

    return 'OAuth ' . implode(', ', $parts);
}
