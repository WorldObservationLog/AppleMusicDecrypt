setTimeout(function () {
    Java.performNow(function () {
        var C3282k = Java.use("c.a.a.e.o.k");
        var m7125s = C3282k.a().s();
        var PurchaseRequest$PurchaseRequestPtr = Java.use("com.apple.android.storeservices.javanative.account.PurchaseRequest$PurchaseRequestPtr");
        function getM3U8(adamID) {
            var c3249t = Java.cast(m7125s, Java.use("c.a.a.e.k.t"));
            var create = PurchaseRequest$PurchaseRequestPtr.create(c3249t.n.value)
            create.get().setProcessDialogActions(true)
            create.get().setURLBagKey("subDownload")
            create.get().setBuyParameters(`salableAdamId=${adamID}&price=0&pricingParameters=SUBS&productType=S`)
            create.get().run()
            var response = create.get().getResponse()
            if (response.get().getError().get() == null){
                var item = response.get().getItems().get(0)
                var assets = item.get().getAssets()
                var size = assets.size()
                return assets.get(size - 1).get().getURL()
            } else {
                return response.get().getError().get().errorCode()
            }
        }
        rpc.exports = {"getm3u8": getM3U8}
    })
}, 8000)