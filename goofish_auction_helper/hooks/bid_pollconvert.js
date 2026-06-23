// Tap-free direct fire: hijack ONE bid.get polling ApiBusiness (which carries a
// real, usable callback) and rewrite its apiName -> bid.price + param ->
// bidPrice=2888800, then let it execute. If the submit honors biz.getApiName()
// we get a real bid.price @ 2888800 through the app's signed path. The response
// handler reports the actual api that went out + the server verdict.

'use strict';
var TARGET_API = 'mtop.idle.vendue.itemdetail.bid.price';
var REPRICE = 2888800;
var FIXED = { auctionId: '76557299', itemId: '1059375760839', vendueId: '76557299' };

function now() { return new Date().toISOString(); }
function emit(e, d) { try { send({ kind: 'call', event: e, data: d, ts: now() }); } catch (x) { console.log('[P] ' + x); } }
function safe(fn) { try { return fn(); } catch (e) { return '<err:' + e + '>'; } }

Java.perform(function () {
  emit('armed', { via: 'poll-convert bid.get -> bid.price', reprice: REPRICE });
  var converted = false;

  try {
    var DCB = Java.use('com.taobao.android.remoteobject.mtopsdk.MtopSDKHandler$DefaultCallBack');
    DCB.onMtopResponseFinished.implementation = function (resp) {
      try {
        var api = String(resp.getApi());
        if (api.indexOf('bid.price') !== -1) {
          emit('mtop_response_bidprice', {
            retCode: String(resp.getRetCode()),
            retMsg: safe(function () { return String(resp.getRetMsg()); }),
            bytedata: safe(function () { return String(resp.getBytedata()); })
          });
        }
      } catch (e) {}
      return this.onMtopResponseFinished(resp);
    };
  } catch (e) { emit('hook_resp_err', { error: String(e) }); }

  try {
    var MtopSend = Java.use('com.taobao.android.remoteobject.easy.MtopSend');
    var ABCls = Java.use('com.taobao.android.remoteobject.easy.ApiBusiness').class;
    var HashMap = Java.use('java.util.HashMap');
    var Long = Java.use('java.lang.Long');

    function setField(biz, name, val) {
      var f = ABCls.getDeclaredField(name);
      f.setAccessible(true);
      f.set(biz, val);
    }

    MtopSend.execute.overload('com.taobao.android.remoteobject.easy.IMtopBusiness').implementation = function (biz) {
      var api = safe(function () { return String(biz.getApiName()); });
      if (!converted && api.indexOf('bid.get') !== -1) {
        converted = true;
        try {
          setField(biz, 'apiName', TARGET_API);
          setField(biz, 'version', '1.0');
          var p = HashMap.$new();
          p.put('auctionId', FIXED.auctionId);
          p.put('bidPrice', Long.valueOf(REPRICE));
          p.put('itemId', FIXED.itemId);
          p.put('vendueId', FIXED.vendueId);
          biz.setParam(p);
          try { biz.isCallBacked().set(false); } catch (e) {}
          emit('converted', {
            apiAfter: safe(function () { return String(biz.getApiName()); }),
            paramAfter: safe(function () { return String(biz.getParam()); })
          });
        } catch (e) { emit('convert_error', { error: String(e), stack: e && e.stack }); }
      }
      return this.execute(biz);
    };
    emit('hooked', {});
  } catch (e) { emit('hook_err', { error: String(e) }); }
});
