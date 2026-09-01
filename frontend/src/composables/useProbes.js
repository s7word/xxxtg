import { reactive } from 'vue'
import { useConfig } from './useConfig'
import { useProxy } from './useProxy'

const { config, applyBaseUrlsTextToConfig } = useConfig()
const { proxyPool, proxyPoolMeta, testing } = useProxy()

const probeTesting = reactive({
  fivesim: false,
  vaksms: false,
  grizzlysms: false,
  smsbower: false,
  antisafety: false,
  reghelp: false
})

const testResults = reactive({
  fivesim: null,
  vaksms: null,
  grizzlysms: null,
  smsbower: null,
  antisafety: null,
  reghelp: null,
  proxyseller: null,
  proxyall: null,
  connectivity: null
})

export const testFiveSim = async () => {
  probeTesting.fivesim = true
  testResults.fivesim = null
  try {
    const res = await fetch('/api/test/fivesim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: config.fivesim_api_key,
        country: config.target_country
      })
    })
    testResults.fivesim = await res.json()
  } catch (e) {
    testResults.fivesim = { success: false, message: e.message }
  } finally {
    probeTesting.fivesim = false
  }
}

export const testGrizzlySms = async () => {
  probeTesting.grizzlysms = true
  testResults.grizzlysms = null
  try {
    const res = await fetch('/api/test/grizzlysms', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: config.grizzly_sms_api_key,
        country: config.target_country
      })
    })
    testResults.grizzlysms = await res.json()
  } catch (e) {
    testResults.grizzlysms = { success: false, message: e.message }
  } finally {
    probeTesting.grizzlysms = false
  }
}

export const testSmsBower = async () => {
  probeTesting.smsbower = true
  testResults.smsbower = null
  try {
    const res = await fetch('/api/test/smsbower', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: config.smsbower_api_key,
        country: config.target_country
      })
    })
    testResults.smsbower = await res.json()
  } catch (e) {
    testResults.smsbower = { success: false, message: e.message }
  } finally {
    probeTesting.smsbower = false
  }
}

export const testVakSms = async () => {
  probeTesting.vaksms = true
  testResults.vaksms = null
  try {
    const res = await fetch('/api/test/vaksms', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: config.vak_sms_api_key, country: config.target_country })
    })
    testResults.vaksms = await res.json()
  } catch (e) {
    testResults.vaksms = { success: false, message: e.message }
  } finally {
    probeTesting.vaksms = false
  }
}

export const testAntiSafety = async () => {
  probeTesting.antisafety = true
  testResults.antisafety = null
  applyBaseUrlsTextToConfig()
  try {
    const res = await fetch('/api/test/antisafety', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: config.antisafety_api_key,
        aid: config.antisafety_aids[config.active_app_type],
        base_urls: config.antisafety_base_urls,
        reporting_base_urls: config.antisafety_reporting_base_urls
      })
    })
    testResults.antisafety = await res.json()
  } catch (e) {
    testResults.antisafety = { success: false, message: e.message }
  } finally {
    probeTesting.antisafety = false
  }
}

export const testRegHelp = async () => {
  probeTesting.reghelp = true
  testResults.reghelp = null
  applyBaseUrlsTextToConfig()
  try {
    const res = await fetch('/api/test/reghelp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: config.reghelp_api_key,
        base_urls: config.reghelp_base_urls
      })
    })
    testResults.reghelp = await res.json()
  } catch (e) {
    testResults.reghelp = { success: false, message: e.message }
  } finally {
    probeTesting.reghelp = false
  }
}

export const testProxySeller = async () => {
  testing.proxyseller = true
  testResults.proxyseller = null
  try {
    const res = await fetch('/api/test/proxyseller', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: config.proxy_seller_key, country: config.target_country })
    })
    testResults.proxyseller = await res.json()
    if (testResults.proxyseller?.data?.proxies) {
      proxyPool.value = testResults.proxyseller.data.proxies
    }
  } catch (e) {
    testResults.proxyseller = { success: false, message: e.message }
  } finally {
    testing.proxyseller = false
  }
}

export const testAllProxySeller = async () => {
  testing.proxyall = true
  testResults.proxyall = null
  try {
    const res = await fetch('/api/proxy-seller/test-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        country: config.target_country,
        api_key: config.proxy_seller_key,
        refresh: false,
        limit: 20
      })
    })
    const data = await res.json()
    testResults.proxyall = data
    if (data.results) proxyPool.value = data.results
    proxyPoolMeta.message = data.message || ''
    proxyPoolMeta.success = data.success
  } catch (e) {
    testResults.proxyall = { success: false, message: e.message }
  } finally {
    testing.proxyall = false
  }
}

export const testProxyConnectivity = async () => {
  testing.connectivity = true
  testResults.connectivity = null
  try {
    const res = await fetch('/api/test/proxy-connectivity', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config.fallback_proxy)
    })
    testResults.connectivity = await res.json()
  } catch (e) {
    testResults.connectivity = { success: false, message: e.message }
  } finally {
    testing.connectivity = false
  }
}

export const useProbes = () => ({
  probeTesting,
  testResults,
  testFiveSim,
  testGrizzlySms,
  testSmsBower,
  testVakSms,
  testAntiSafety,
  testRegHelp,
  testProxySeller,
  testAllProxySeller,
  testProxyConnectivity
})
