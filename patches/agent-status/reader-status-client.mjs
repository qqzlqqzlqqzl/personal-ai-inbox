// Reuse Reader's existing apiClient. Never read storage, construct auth or copy tokens.
export function createReaderStatusClient(apiClient, serverURL, pageURL) {
  const page=new URL(pageURL),server=new URL(serverURL,page);
  if(server.origin!==page.origin || !['/mf','/mf/'].includes(server.pathname))throw new Error('Status requires the same-origin Reader gateway');
  return ()=>apiClient.get('v1/ai/agent-status',{cache:'no-store',redirect:'error',signal:AbortSignal.timeout(10000)});
}