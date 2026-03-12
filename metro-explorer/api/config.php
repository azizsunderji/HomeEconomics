<?php
// Bridge: post-social.php expects config.php returning an array.
// Credentials live in config.json (also used by the JS frontend).
$json = file_get_contents(__DIR__ . '/config.json');
return json_decode($json, true);
