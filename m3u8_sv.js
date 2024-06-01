setTimeout(function () {
    Java.performNow(function () {
        Java.use("com.apple.android.music.common.MainContentActivity");
        var SVPlaybackLeaseManagerProxy;
        Java.choose("com.apple.android.music.playback.SVPlaybackLeaseManagerProxy", {
            onMatch: function (x) {
                SVPlaybackLeaseManagerProxy = x
            },
            onComplete: function (x) {}
        });
        var HLSParam = Java.array('java.lang.String', ["HLS"])
        function getM3U8(adamID) {
            var MediaAssetInfo = SVPlaybackLeaseManagerProxy.requestAsset(parseInt(adamID), HLSParam, false)
            if (MediaAssetInfo === null) {
                return -1
            }
            return MediaAssetInfo.getDownloadUrl()
        }
        rpc.exports = {"getm3u8": getM3U8}
    })
}, 8000)