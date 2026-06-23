// Focused, read-only Frida hook for Goofish auction/bid MTOP calls.
// Emits compact JSON lines prefixed with [XY_BID].

'use strict';

var state = {};
var interesting = /(vendue|auction|bid|price|makebid|bidprice)/i;

function now() {
  return new Date().toISOString();
}

function text(value, limit) {
  limit = limit || 3000;
  if (value === null || value === undefined) {
    return String(value);
  }
  try {
    var s = value.toString();
    return s.length > limit ? s.substring(0, limit) + '...<truncated>' : s;
  } catch (e) {
    return '<toString failed: ' + e + '>';
  }
}

function log(kind, data) {
  console.log('[XY_BID] ' + JSON.stringify({ ts: now(), kind: kind, data: data }));
}

function stack() {
  try {
    var Log = Java.use('android.util.Log');
    var Throwable = Java.use('java.lang.Throwable');
    return text(Log.getStackTraceString(Throwable.$new()), 2200);
  } catch (e) {
    return '<stack failed: ' + e + '>';
  }
}

function objectId(obj) {
  try {
    var System = Java.use('java.lang.System');
    return String(System.identityHashCode(obj));
  } catch (e) {
    return text(obj);
  }
}

function isInteresting(s) {
  return interesting.test(String(s || ''));
}

function ensure(id) {
  if (!state[id]) {
    state[id] = {};
  }
  return state[id];
}

function emitRequest(id, reason) {
  var r = ensure(id);
  var combined = [r.apiName, r.version, r.data, r.toString].join(' ');
  if (!isInteresting(combined)) {
    return;
  }
  log('mtop_request', {
    id: id,
    reason: reason,
    apiName: r.apiName,
    version: r.version,
    data: r.data,
    needEcode: r.needEcode,
    needSession: r.needSession,
    request: r.toString,
    stack: r.stack
  });
}

function hookMtopRequest() {
  var Req = Java.use('mtopsdk.mtop.domain.MtopRequest');
  [
    ['setApiName', 'apiName'],
    ['setVersion', 'version'],
    ['setData', 'data'],
    ['setNeedEcode', 'needEcode'],
    ['setNeedSession', 'needSession']
  ].forEach(function (pair) {
    var method = pair[0];
    var field = pair[1];
    Req[method].overloads.forEach(function (ov) {
      ov.implementation = function () {
        var id = objectId(this);
        var r = ensure(id);
        r[field] = text(arguments[0]);
        if (field === 'apiName' || field === 'data') {
          r.stack = stack();
        }
        var ret = ov.apply(this, arguments);
        emitRequest(id, method);
        return ret;
      };
    });
  });
  ['getApiName', 'getVersion', 'getData', 'toString'].forEach(function (method) {
    Req[method].overloads.forEach(function (ov) {
      ov.implementation = function () {
        var ret = ov.apply(this, arguments);
        var id = objectId(this);
        var r = ensure(id);
        if (method === 'getApiName') r.apiName = text(ret);
        if (method === 'getVersion') r.version = text(ret);
        if (method === 'getData') r.data = text(ret);
        if (method === 'toString') r.toString = text(ret);
        emitRequest(id, method);
        return ret;
      };
    });
  });
}

function hookMethod(className, methodName, options) {
  options = options || {};
  try {
    var Cls = Java.use(className);
    if (!Cls[methodName] || !Cls[methodName].overloads) return;
    Cls[methodName].overloads.forEach(function (ov) {
      var argTypes = ov.argumentTypes.map(function (t) { return t.name; });
      ov.implementation = function () {
        var args = [];
        for (var i = 0; i < arguments.length; i++) args.push(text(arguments[i]));
        var payload = {
          className: className,
          methodName: methodName,
          argumentTypes: argTypes,
          args: args
        };
        if (options.stack) payload.stack = stack();
        var shouldLog = options.always || isInteresting(args.join(' '));
        var ret = ov.apply(this, arguments);
        if (shouldLog || isInteresting(text(ret))) {
          payload.result = text(ret);
          log('call', payload);
        }
        return ret;
      };
      log('hooked', { className: className, methodName: methodName, argumentTypes: argTypes });
    });
  } catch (e) {
    log('hook_error', { className: className, methodName: methodName, error: String(e) });
  }
}

function hookDeclaredMethods(className) {
  try {
    var Cls = Java.use(className);
    var methods = Cls.class.getDeclaredMethods();
    var names = {};
    for (var i = 0; i < methods.length; i++) names[text(methods[i].getName())] = true;
    Object.keys(names).forEach(function (name) {
      hookMethod(className, name, { always: true, stack: true });
    });
    if (Cls.$init && Cls.$init.overloads) {
      Cls.$init.overloads.forEach(function (ov) {
        ov.implementation = function () {
          var args = [];
          for (var i = 0; i < arguments.length; i++) args.push(text(arguments[i]));
          log('call', { className: className, methodName: '$init', args: args, stack: stack() });
          return ov.apply(this, arguments);
        };
      });
    }
  } catch (e) {
    log('hook_error', { className: className, methodName: '<declared>', error: String(e) });
  }
}

function main() {
  if (typeof Java === 'undefined') {
    setTimeout(main, 500);
    return;
  }
  Java.perform(function () {
    log('agent_loaded', { package: 'com.taobao.idlefish', arch: Process.arch });
    hookMtopRequest();
    hookMethod('com.taobao.tao.remotebusiness.MtopBusiness', 'build', { always: true, stack: true });
    hookMethod('com.taobao.tao.remotebusiness.MtopBusiness', 'startRequest', { always: false, stack: true });
    hookMethod('mtopsdk.mtop.intf.MtopBuilder', 'asyncRequest', { always: false, stack: true });
    hookMethod('mtopsdk.mtop.intf.MtopBuilder', 'syncRequest', { always: false, stack: true });
    hookMethod('com.taobao.android.remoteobject.easy.MtopSend', 'execute', { always: true, stack: true });
    hookMethod('com.taobao.android.remoteobject.mtopsdk.ext.MtopExtSDKHandler', 'process', { always: true, stack: true });
    hookMethod('com.taobao.android.remoteobject.mtopsdk.ext.MtopExtSDKHandler', 'originReq', { always: true, stack: true });
    [
      'com.taobao.fleamarket.business.bidprice.api.ApiMakeBidPriceRequest',
      'com.taobao.fleamarket.business.bidprice.api.ApiMakeBidPriceMyRequest',
      'com.taobao.fleamarket.business.bidprice.api.ApiMakeBidPriceResponse'
    ].forEach(hookDeclaredMethods);
    log('agent_ready', {});
  });
}

setImmediate(main);
