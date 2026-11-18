/**
 * Azure icons adapter using Fluent UI React Icons
 * Provides a normalized interface for Azure-relevant icons
 */

import { createElement, type FC, type CSSProperties } from 'react';
import {
  Cloud20Regular,
  Server20Regular,
  Database20Regular,
  Storage20Regular,
  PlugConnected20Regular,
  Shield20Regular,
  Code20Regular,
  Apps20Regular,
  Globe20Regular,
  Key20Regular,
  Checkmark20Regular,
  CheckmarkCircle20Regular,
  Warning20Regular,
  ErrorCircle20Regular,
  Folder20Regular,
  Image20Regular,
  Video20Regular,
  Filter20Regular,
  Search20Regular,
  Settings20Regular,
  Code20Filled,
  DataBarVertical20Regular,
  Connector20Regular,
  Link20Regular,
  WindowDevTools20Regular,
  DocumentMultiple20Regular,
  Accessibility20Regular,
  Tag20Regular,
  Router20Regular,
} from '@fluentui/react-icons';

import type { IconComponent } from '../types';

/**
 * Wrapper component factory that normalizes IconProps
 * Converts size and color props to className and style
 */
const createIconWrapper = (
  FluentIcon: FC<{ className?: string; style?: CSSProperties }>,
  defaultSize: number = 24
): IconComponent => {
  const Wrapper: IconComponent = ({ className = '', size = defaultSize, color }) => {
    const sizeNum = typeof size === 'string' ? parseInt(size, 10) : size;
    const style: CSSProperties = {};

    if (color) {
      style.color = color;
    }

    return createElement(FluentIcon, {
      className,
      style: {
        width: sizeNum,
        height: sizeNum,
        ...style,
      },
    });
  };

  Wrapper.displayName = `AzureIcon(${FluentIcon.displayName || 'Unknown'})`;
  return Wrapper;
};

// Azure icon components mapped to normalized interface
export const azureIconMap: Record<string, IconComponent> = {
  // Cloud and Infrastructure
  'azure-cloud': createIconWrapper(Cloud20Regular),
  'azure-server': createIconWrapper(Server20Regular),
  'azure-virtual-machine': createIconWrapper(Server20Regular),
  'azure-network': createIconWrapper(Router20Regular),
  'azure-virtual-network': createIconWrapper(Globe20Regular),
  'azure-vpn': createIconWrapper(PlugConnected20Regular),
  'azure-load-balancer': createIconWrapper(Router20Regular),
  'azure-cdn': createIconWrapper(Globe20Regular),
  'azure-front-door': createIconWrapper(Globe20Regular),
  'azure-traffic-manager': createIconWrapper(Connector20Regular),

  // Data and Storage
  'azure-database': createIconWrapper(Database20Regular),
  'azure-storage': createIconWrapper(Storage20Regular),
  'azure-sql-database': createIconWrapper(Database20Regular),
  'azure-sql-server': createIconWrapper(Server20Regular),
  'azure-cosmos-db': createIconWrapper(Database20Regular),
  'azure-data-lake': createIconWrapper(Storage20Regular),
  'azure-synapse': createIconWrapper(DataBarVertical20Regular),

  // Security and Compliance
  'azure-security': createIconWrapper(Shield20Regular),
  'azure-firewall': createIconWrapper(Shield20Regular),
  'azure-key-vault': createIconWrapper(Key20Regular),
  'azure-lock': createIconWrapper(Key20Regular),
  'azure-compliance': createIconWrapper(CheckmarkCircle20Regular),

  // Application and Code
  'azure-app-service': createIconWrapper(Apps20Regular),
  'azure-aks': createIconWrapper(Apps20Regular),
  'azure-functions': createIconWrapper(Code20Regular),
  'azure-logic-apps': createIconWrapper(Link20Regular),
  'azure-code': createIconWrapper(Code20Filled),
  'azure-devops': createIconWrapper(WindowDevTools20Regular),
  'azure-pipeline': createIconWrapper(Connector20Regular),

  // Integration and Messaging
  'azure-service-bus': createIconWrapper(PlugConnected20Regular),
  'azure-event-hub': createIconWrapper(Connector20Regular),
  'azure-api-management': createIconWrapper(PlugConnected20Regular),

  // Data and Analytics
  'azure-data-factory': createIconWrapper(Settings20Regular),
  'azure-monitor': createIconWrapper(DataBarVertical20Regular),
  'azure-application-insights': createIconWrapper(Search20Regular),
  'azure-log-analytics': createIconWrapper(Search20Regular),

  // Storage and Files
  'azure-container-registry': createIconWrapper(Database20Regular),
  'azure-documents': createIconWrapper(DocumentMultiple20Regular),
  'azure-file-share': createIconWrapper(Folder20Regular),
  'azure-blob': createIconWrapper(Storage20Regular),

  // Additional Azure Services
  'azure-warning': createIconWrapper(Warning20Regular),
  'azure-error': createIconWrapper(ErrorCircle20Regular),
  'azure-settings': createIconWrapper(Settings20Regular),
  'azure-search': createIconWrapper(Search20Regular),
  'azure-filter': createIconWrapper(Filter20Regular),
  'azure-image': createIconWrapper(Image20Regular),
  'azure-video': createIconWrapper(Video20Regular),
  'azure-accessibility': createIconWrapper(Accessibility20Regular),
  'azure-tags': createIconWrapper(Tag20Regular),
  'azure-checkmark': createIconWrapper(Checkmark20Regular),
}

export default azureIconMap;
