import { SpdkChecksProvider } from './checks-provider';

// @ts-ignore - Gerrit global
window.Gerrit.install((plugin: any) => {
  const checksApi = plugin.checks();
  checksApi.register(new SpdkChecksProvider(plugin), {
    fetchPollingIntervalSeconds: 30,
  });
});
