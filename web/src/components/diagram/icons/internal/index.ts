// Re-export all cloud provider icons
export {
  AwsIcon,
  AzureIcon,
  GcpIcon,
  KubernetesIcon,
  DockerIcon,
  DigitalOceanIcon,
  OracleCloudIcon,
} from './cloud-providers';

// Re-export all infrastructure icons
export {
  ServerIcon,
  VirtualMachineIcon,
  ContainerIcon,
  LambdaIcon,
} from './infrastructure';

// Re-export all database icons
export {
  DatabaseIcon,
  RedisIcon,
  MongoDbIcon,
  PostgresIcon,
  MySqlIcon,
} from './database';

// Re-export all networking icons
export {
  NetworkIcon,
  RouterIcon,
  SwitchIcon,
  FirewallIcon,
  LoadBalancerIcon,
  VpnIcon,
  DnsIcon,
  CdnIcon,
} from './networking';

// Re-export categories
export { internalCategories } from './categories';

// Create combined icon map for internal icons
import {
  AwsIcon,
  AzureIcon,
  GcpIcon,
  KubernetesIcon,
  DockerIcon,
  DigitalOceanIcon,
  OracleCloudIcon,
} from './cloud-providers';
import {
  ServerIcon,
  VirtualMachineIcon,
  ContainerIcon,
  LambdaIcon,
} from './infrastructure';
import {
  DatabaseIcon,
  RedisIcon,
  MongoDbIcon,
  PostgresIcon,
  MySqlIcon,
} from './database';
import {
  NetworkIcon,
  RouterIcon,
  SwitchIcon,
  FirewallIcon,
  LoadBalancerIcon,
  VpnIcon,
  DnsIcon,
  CdnIcon,
} from './networking';

export const internalIconMap = {
  // Cloud providers
  aws: AwsIcon,
  azure: AzureIcon,
  gcp: GcpIcon,
  kubernetes: KubernetesIcon,
  docker: DockerIcon,
  digitalocean: DigitalOceanIcon,
  oracle: OracleCloudIcon,

  // Infrastructure
  server: ServerIcon,
  vm: VirtualMachineIcon,
  container: ContainerIcon,
  lambda: LambdaIcon,

  // Database
  database: DatabaseIcon,
  redis: RedisIcon,
  mongodb: MongoDbIcon,
  postgres: PostgresIcon,
  mysql: MySqlIcon,

  // Networking
  network: NetworkIcon,
  router: RouterIcon,
  switch: SwitchIcon,
  firewall: FirewallIcon,
  loadbalancer: LoadBalancerIcon,
  vpn: VpnIcon,
  dns: DnsIcon,
  cdn: CdnIcon,
};
