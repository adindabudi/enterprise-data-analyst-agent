param environmentName string
param suffix string

var compactEnvironmentName = take(toLower(replace(environmentName, '-', '')), 10)

output names object = {
  containerRegistry: take('acreda${compactEnvironmentName}${suffix}', 50)
  cosmosAccount: take('cosmoseda${compactEnvironmentName}${suffix}', 44)
  durableTaskScheduler: take('dts-eda-${environmentName}-${suffix}', 64)
  foundryAccount: take('foundry-eda-${environmentName}-${suffix}-v2', 64)
  keyVault: take('kveda${compactEnvironmentName}${suffix}', 24)
  managedEnvironment: take('cae-eda-${environmentName}-${suffix}', 32)
  redisEnterprise: take('redis-eda-${environmentName}-${suffix}', 63)
  sessionPool: take('session-eda-${environmentName}-${suffix}', 60)
  storageAccount: take('steda${compactEnvironmentName}${suffix}', 24)
}