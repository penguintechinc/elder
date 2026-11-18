// Comprehensive Icon Library for IceCharts Diagram Editor
// Categories: Cloud, Infrastructure, Networking, Security, Database, Monitoring, DevOps, General

import React from 'react';

type IconProps = {
  className?: string;
};

// ============================================
// CLOUD PROVIDERS
// ============================================

export const AwsIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M6.763 10.036c0 .296.032.535.088.71.064.176.144.368.256.576.04.063.056.127.056.183 0 .08-.048.16-.152.24l-.503.335a.383.383 0 0 1-.208.072c-.08 0-.16-.04-.239-.112a2.47 2.47 0 0 1-.287-.375 6.18 6.18 0 0 1-.248-.471c-.622.734-1.405 1.101-2.347 1.101-.67 0-1.205-.191-1.596-.574-.391-.384-.59-.894-.59-1.533 0-.678.239-1.23.726-1.644.487-.415 1.133-.623 1.955-.623.272 0 .551.024.846.064.296.04.6.104.918.176v-.583c0-.607-.127-1.03-.375-1.277-.255-.248-.686-.367-1.3-.367-.28 0-.568.031-.863.103-.295.072-.583.16-.862.272a2.287 2.287 0 0 1-.28.104.488.488 0 0 1-.127.023c-.112 0-.168-.08-.168-.247v-.391c0-.128.016-.224.056-.28a.597.597 0 0 1 .224-.167c.279-.144.614-.264 1.005-.36a4.84 4.84 0 0 1 1.246-.151c.95 0 1.644.216 2.091.647.439.43.662 1.085.662 1.963v2.586zm-3.24 1.214c.263 0 .534-.048.822-.144.287-.096.543-.271.758-.51.128-.152.224-.32.272-.512.047-.191.08-.423.08-.694v-.335a6.66 6.66 0 0 0-.735-.136 6.02 6.02 0 0 0-.75-.048c-.535 0-.926.104-1.19.32-.263.215-.39.518-.39.917 0 .375.095.655.295.846.191.2.47.296.838.296zm6.41.862c-.144 0-.24-.024-.304-.08-.064-.048-.12-.16-.168-.311L7.586 5.55a1.398 1.398 0 0 1-.072-.32c0-.128.064-.2.191-.2h.783c.151 0 .255.025.31.08.065.048.113.16.16.312l1.342 5.284 1.245-5.284c.04-.16.088-.264.151-.312a.549.549 0 0 1 .32-.08h.638c.152 0 .256.025.32.08.063.048.12.16.151.312l1.261 5.348 1.381-5.348c.048-.16.104-.264.16-.312a.52.52 0 0 1 .311-.08h.743c.127 0 .2.065.2.2 0 .04-.009.08-.017.128a1.137 1.137 0 0 1-.056.2l-1.923 6.17c-.048.16-.104.263-.168.311a.51.51 0 0 1-.303.08h-.687c-.151 0-.255-.024-.32-.08-.063-.056-.119-.16-.15-.32l-1.238-5.148-1.23 5.14c-.04.16-.087.264-.15.32-.065.056-.177.08-.32.08zm10.256.215c-.415 0-.83-.048-1.229-.143-.399-.096-.71-.2-.918-.32-.128-.071-.215-.151-.247-.223a.563.563 0 0 1-.048-.224v-.407c0-.167.064-.247.183-.247.048 0 .096.008.144.024.048.016.12.048.2.08.271.12.566.215.878.279.319.064.63.096.95.096.502 0 .894-.088 1.165-.264a.86.86 0 0 0 .415-.758.777.777 0 0 0-.215-.559c-.144-.151-.416-.287-.807-.415l-1.157-.36c-.583-.183-1.014-.454-1.277-.813a1.902 1.902 0 0 1-.4-1.158c0-.335.073-.63.216-.886.144-.255.335-.479.575-.654.24-.184.51-.32.83-.415.32-.096.655-.136 1.006-.136.175 0 .359.008.535.032.183.024.35.056.518.088.16.04.312.08.455.127.144.048.256.096.336.144a.69.69 0 0 1 .24.2.43.43 0 0 1 .071.263v.375c0 .168-.064.256-.184.256a.83.83 0 0 1-.303-.096 3.652 3.652 0 0 0-1.532-.311c-.455 0-.815.071-1.062.223-.248.152-.375.383-.375.71 0 .224.08.416.24.567.159.152.454.304.877.44l1.134.358c.574.184.99.44 1.237.767.247.327.367.702.367 1.117 0 .343-.072.655-.207.926-.144.272-.336.511-.583.703-.248.2-.543.343-.886.447-.36.111-.734.167-1.142.167z"/>
  </svg>
);

export const AzureIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M5.483 21.3H24L14.025 4.013l-3.038 8.347 5.836 6.938L5.483 21.3zM13.23 2.7L6.105 8.677 0 19.253h5.505l7.725-16.553z"/>
  </svg>
);

export const GcpIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12.19 2.38a9.344 9.344 0 0 0-9.234 6.893c.053-.02-.055.013 0 0-3.875 2.551-3.922 8.11-.247 10.941l.006-.007-.007.03a6.717 6.717 0 0 0 4.077 1.356h5.173l.03.03h5.192c6.687.053 9.376-8.605 3.835-12.35a9.365 9.365 0 0 0-8.825-6.893z"/>
  </svg>
);

export const KubernetesIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M10.204 14.35l.007.01-.999 2.413a5.171 5.171 0 0 1-2.075-2.597l2.578-.437.004.005a.44.44 0 0 1 .484.606zm-.833-2.129a.44.44 0 0 0 .173-.756l.002-.011L7.585 9.7a5.143 5.143 0 0 0-.73 3.255l2.514-.725.002-.009zm1.145-1.98a.44.44 0 0 0 .699-.337l-.01-.02.15-2.62a5.144 5.144 0 0 0-3.01 1.442l2.164 1.548.007-.013zm2.369-1.898l-.009.02.15 2.62a.44.44 0 0 0 .699.337l.007.013 2.164-1.548a5.144 5.144 0 0 0-3.01-1.442zm2.262 3.673l.002.009 2.514.725a5.143 5.143 0 0 0-.73-3.255l-1.96 1.754.002.011a.44.44 0 0 0 .172.756zm-.695 1.209l.004-.005 2.578.437a5.171 5.171 0 0 1-2.075 2.597l-.999-2.413.007-.01a.44.44 0 0 1 .485-.606zM12 6.042c-.48 0-.933.097-1.353.27l-.034-.064-.363.182a5.17 5.17 0 0 0-1.982 1.834l-.004.006-.326.466h-.002a5.164 5.164 0 0 0-.755 2.229l.015.071-.07.39a5.174 5.174 0 0 0 .418 2.542l.066.124-.16.422a5.17 5.17 0 0 0 1.636 2.03l.096.066.27.336a5.168 5.168 0 0 0 2.332.96l.138.008.377.146c.18.024.364.036.549.036h.003c.185 0 .369-.012.549-.036l.377-.146.138-.008a5.168 5.168 0 0 0 2.332-.96l.27-.336.096-.066a5.17 5.17 0 0 0 1.636-2.03l-.16-.422.066-.124a5.174 5.174 0 0 0 .418-2.542l-.07-.39.015-.071a5.164 5.164 0 0 0-.755-2.229h-.002l-.326-.466-.004-.006a5.17 5.17 0 0 0-1.982-1.834l-.363-.182-.034.064A4.924 4.924 0 0 0 12 6.042z"/>
  </svg>
);

export const DockerIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M13.983 11.078h2.119a.186.186 0 0 0 .186-.185V9.006a.186.186 0 0 0-.186-.186h-2.119a.185.185 0 0 0-.185.185v1.888c0 .102.083.185.185.185m-2.954-5.43h2.118a.186.186 0 0 0 .186-.186V3.574a.186.186 0 0 0-.186-.185h-2.118a.185.185 0 0 0-.185.185v1.888c0 .102.082.185.185.186m0 2.716h2.118a.187.187 0 0 0 .186-.186V6.29a.186.186 0 0 0-.186-.185h-2.118a.185.185 0 0 0-.185.185v1.887c0 .102.082.185.185.186m-2.93 0h2.12a.186.186 0 0 0 .184-.186V6.29a.185.185 0 0 0-.185-.185H8.1a.185.185 0 0 0-.185.185v1.887c0 .102.083.185.185.186m-2.964 0h2.119a.186.186 0 0 0 .185-.186V6.29a.186.186 0 0 0-.185-.185H5.136a.186.186 0 0 0-.186.185v1.887c0 .102.084.185.186.186m5.893 2.715h2.118a.186.186 0 0 0 .186-.185V9.006a.186.186 0 0 0-.186-.186h-2.118a.185.185 0 0 0-.185.185v1.888c0 .102.082.185.185.185m-2.93 0h2.12a.185.185 0 0 0 .184-.185V9.006a.185.185 0 0 0-.184-.186h-2.12a.185.185 0 0 0-.184.185v1.888c0 .102.083.185.185.185m-2.964 0h2.119a.185.185 0 0 0 .185-.185V9.006a.185.185 0 0 0-.185-.186h-2.119a.186.186 0 0 0-.186.186v1.887c0 .102.084.185.186.185m-2.92 0h2.12a.185.185 0 0 0 .184-.185V9.006a.185.185 0 0 0-.184-.186h-2.12a.185.185 0 0 0-.184.185v1.888c0 .102.082.185.185.185M23.763 9.89c-.065-.051-.672-.51-1.954-.51-.338.001-.676.03-1.01.087-.248-1.7-1.653-2.53-1.716-2.566l-.344-.199-.226.327c-.284.438-.49.922-.612 1.43-.23.97-.09 1.882.403 2.661-.595.332-1.55.413-1.744.42H.751a.751.751 0 0 0-.75.748 11.376 11.376 0 0 0 .692 4.062c.545 1.428 1.355 2.48 2.41 3.124 1.18.723 3.1 1.137 5.275 1.137.983.003 1.963-.086 2.93-.266a12.248 12.248 0 0 0 3.823-1.389c.98-.567 1.86-1.288 2.61-2.136 1.252-1.418 1.998-2.997 2.553-4.4h.221c1.372 0 2.215-.549 2.68-1.009.309-.293.55-.65.707-1.046l.098-.288Z"/>
  </svg>
);

export const DigitalOceanIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12.04 0C5.408-.02.005 5.37.005 11.992h4.638c0-4.923 4.882-8.731 10.064-6.855a6.95 6.95 0 014.147 4.148c1.889 5.177-1.924 10.055-6.84 10.064v-4.61H7.391v4.623h4.61V24c7.86 0 13.834-7.428 11.204-15.597a11.94 11.94 0 00-7.62-7.617A12.02 12.02 0 0012.04 0zM7.39 19.362H3.828v3.564H7.39zm-3.563 3.563H.9V20.29h2.928z"/>
  </svg>
);

export const OracleCloudIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M7.076 7.076a6.927 6.927 0 100 9.848 6.927 6.927 0 000-9.848zm-.864 8.12a5.177 5.177 0 11.001-6.392 5.177 5.177 0 01-.001 6.392zM16.924 7.076a6.927 6.927 0 100 9.848 6.927 6.927 0 000-9.848zm-.865 8.12a5.177 5.177 0 11.001-6.392 5.177 5.177 0 01-.001 6.392z"/>
  </svg>
);

// ============================================
// INFRASTRUCTURE - Servers & Compute
// ============================================

export const ServerIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="2" width="20" height="8" rx="2" />
    <rect x="2" y="14" width="20" height="8" rx="2" />
    <circle cx="6" cy="6" r="1" fill="currentColor" />
    <circle cx="6" cy="18" r="1" fill="currentColor" />
    <line x1="10" y1="6" x2="18" y2="6" />
    <line x1="10" y1="18" x2="18" y2="18" />
  </svg>
);

export const VirtualMachineIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="3" width="20" height="14" rx="2" />
    <path d="M8 21h8M12 17v4" />
    <path d="M7 7h10M7 10h6" />
  </svg>
);

export const ContainerIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
    <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
    <line x1="12" y1="22.08" x2="12" y2="12" />
  </svg>
);

export const LambdaIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M4 4h4l4 8 4-8h4l-6 10 6 10h-4l-4-8-4 8H4l6-10z"/>
  </svg>
);

// ============================================
// INFRASTRUCTURE - Database
// ============================================

export const DatabaseIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <ellipse cx="12" cy="5" rx="9" ry="3" />
    <path d="M21 5v14c0 1.657-4.03 3-9 3s-9-1.343-9-3V5" />
    <path d="M21 12c0 1.657-4.03 3-9 3s-9-1.343-9-3" />
  </svg>
);

export const RedisIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M10.5 2.661l.54.997-1.797.644 2.409.218.748 1.246.467-1.35 2.469-.222-1.761-.59.508-.963-1.832.924zm8.5 5.73c0 .142-.016.277-.039.41-.218 1.293-2.023 2.404-4.961 2.404-2.938 0-4.743-1.111-4.961-2.404a1.616 1.616 0 0 1-.039-.41c0-1.409 2.238-2.55 5-2.55s5 1.141 5 2.55zm-5 4.35c2.762 0 5-1.141 5-2.55v2.55c0 1.409-2.238 2.55-5 2.55s-5-1.141-5-2.55v-2.55c0 1.409 2.238 2.55 5 2.55zm0 3.15c2.762 0 5-1.141 5-2.55v2.55c0 1.409-2.238 2.55-5 2.55s-5-1.141-5-2.55v-2.55c0 1.409 2.238 2.55 5 2.55zm0 3.15c2.762 0 5-1.141 5-2.55v2.55c0 1.409-2.238 2.55-5 2.55s-5-1.141-5-2.55v-2.55c0 1.409 2.238 2.55 5 2.55z"/>
  </svg>
);

export const MongoDbIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M17.193 9.555c-1.264-5.58-4.252-7.414-4.573-8.115-.28-.394-.53-.954-.735-1.44-.036.495-.055.685-.523 1.184-.723.566-4.438 3.682-4.74 10.02-.282 5.912 4.27 9.435 4.888 9.884l.07.05A73.49 73.49 0 0111.91 24h.481c.114-1.032.284-2.056.51-3.07.417-.296.604-.463.85-.693a11.342 11.342 0 003.639-8.464c.01-.814-.103-1.662-.197-2.218zm-5.336 8.195s0-8.291.275-8.29c.213 0 .49 10.695.49 10.695-.381-.045-.765-1.76-.765-2.405z"/>
  </svg>
);

export const PostgresIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M17.128 0a10.134 10.134 0 00-2.755.403l-.063.02A10.922 10.922 0 0012.6.258C11.422.238 10.41.524 9.594 1 8.79.721 7.122.24 5.364.336 4.14.403 2.804.775 1.814 1.82.827 2.865.305 4.482.415 6.682c.03.607.203 1.597.49 2.879s.69 2.783 1.193 4.152c.503 1.37 1.054 2.6 1.915 3.436.43.419 1.022.771 1.72.742.49-.02.933-.235 1.315-.552.186.245.385.352.566.451.228.125.45.21.68.266.413.103 1.12.241 1.948.1.282-.047.579-.058.088.378l-.493.308a2.6 2.6 0 00-.313.219c-.787.583-1.277 1.49-1.222 2.561.042.836.199 1.549.803 2.072.602.524 1.298.812 2.225.86.926.05 1.063-.08 1.605-.287.542-.206 1.043-.59 1.648-1.31.475-.565.86-1.204 1.078-1.919l.345-1.131c.2.07.405.098.618.1.686.004 1.07-.299 1.2-.39a10.483 10.483 0 001.908-2.216c.263.074.554.132.87.165 1.274.132 2.39-.107 3.227-.655.837-.547 1.333-1.312 1.574-2.104.241-.793.263-1.567.142-2.19-.122-.624-.353-1.1-.534-1.394l-.104-.168c.043-.066.088-.133.122-.204.222-.46.332-.915.35-1.372.017-.457-.073-.904-.23-1.313a3.61 3.61 0 00-.628-1.022c.061-.432.1-.89.095-1.373-.005-.543-.085-1.154-.33-1.795-.246-.64-.695-1.28-1.463-1.745-.647-.392-1.323-.562-1.978-.628a6.727 6.727 0 00-1.655.042zM9.087 12.976a5.281 5.281 0 01-2.061-.396c-.376-.169-.702-.315-.94-.47-.237-.154-.362-.265-.369-.309a.35.35 0 01.05-.202c.054-.08.127-.145.223-.194.095-.049.21-.086.337-.11.127-.024.263-.035.402-.035h.031c.283.005.566.058.838.137.271.079.532.183.77.304.239.12.455.256.635.396.18.14.323.283.417.413.093.13.136.243.123.323-.013.08-.08.127-.19.143-.111.016-.265 0-.265 0z"/>
  </svg>
);

export const MySqlIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M16.405 5.501c-.115 0-.193.014-.274.033v.013h.014c.054.104.146.18.214.273.054.107.1.214.154.32l.014-.015c.094-.066.14-.172.14-.333-.04-.047-.046-.094-.08-.14-.04-.067-.126-.1-.18-.153zM5.77 18.695h-.927a50.854 50.854 0 00-.27-4.41h-.008l-1.41 4.41H2.45l-1.4-4.41h-.01a72.892 72.892 0 00-.195 4.41H0c.055-1.966.192-3.81.41-5.53h1.15l1.335 4.064h.008l1.347-4.063h1.095c.242 2.015.384 3.86.428 5.53zm4.017-4.08c-.378 2.045-.876 3.533-1.492 4.46-.482.723-1.01 1.084-1.583 1.084-.16 0-.36-.04-.6-.118v-.477c.115.02.24.027.375.027.36 0 .65-.115.865-.343.238-.258.357-.6.357-1.023 0-.26-.07-.627-.21-1.1L6.2 14.615h.914l.78 3.157c.18.69.27 1.1.27 1.233 0 .18-.06.32-.175.42l.007.007c.41-.72.706-1.59.886-2.612zm12.766 4.08h-2.92v-5.53h.93v4.782h1.99zm-3.837-3.145l-.004-.004c-.283.36-.547.592-.79.696-.242.103-.507.155-.797.155-.387 0-.69-.088-.91-.266-.22-.177-.33-.44-.33-.79 0-.49.167-.88.5-1.17.333-.29.81-.43 1.43-.43.17 0 .38.015.62.042v-.192c0-.49-.25-.735-.75-.735-.35 0-.68.082-1.02.247l-.126-.427c.424-.195.87-.293 1.337-.293.42 0 .738.11.95.33.212.22.32.55.32.99v2.39h-.43zm-.27-1.33c-.28-.05-.48-.07-.6-.07-.367 0-.656.08-.87.24-.213.16-.32.39-.32.68 0 .227.06.396.184.512.125.115.296.173.513.173.186 0 .363-.04.53-.12.168-.08.3-.19.4-.327zm-1.84-2.02c0-.133.044-.245.13-.334.087-.088.196-.132.33-.132.132 0 .242.044.33.132.088.09.132.2.132.334s-.044.243-.132.33a.45.45 0 01-.33.13.45.45 0 01-.33-.13.443.443 0 01-.13-.33z"/>
  </svg>
);

// ============================================
// NETWORKING
// ============================================

export const NetworkIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="12" r="3" />
    <circle cx="4" cy="6" r="2" />
    <circle cx="20" cy="6" r="2" />
    <circle cx="4" cy="18" r="2" />
    <circle cx="20" cy="18" r="2" />
    <path d="M6 7l4 3M14 10l4-3M6 17l4-3M14 14l4 3" />
  </svg>
);

export const RouterIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="10" width="20" height="8" rx="2" />
    <path d="M6 10V6a2 2 0 012-2h8a2 2 0 012 2v4" />
    <circle cx="6" cy="14" r="1" fill="currentColor" />
    <circle cx="10" cy="14" r="1" fill="currentColor" />
    <line x1="14" y1="14" x2="18" y2="14" />
  </svg>
);

export const SwitchIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="8" width="20" height="8" rx="2" />
    <line x1="6" y1="4" x2="6" y2="8" />
    <line x1="12" y1="4" x2="12" y2="8" />
    <line x1="18" y1="4" x2="18" y2="8" />
    <line x1="6" y1="16" x2="6" y2="20" />
    <line x1="12" y1="16" x2="12" y2="20" />
    <line x1="18" y1="16" x2="18" y2="20" />
  </svg>
);

export const FirewallIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="4" width="20" height="16" rx="2" />
    <path d="M2 8h20M2 12h20M2 16h20" />
    <path d="M7 4v16M12 4v16M17 4v16" />
  </svg>
);

export const LoadBalancerIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="5" r="3" />
    <circle cx="5" cy="19" r="3" />
    <circle cx="12" cy="19" r="3" />
    <circle cx="19" cy="19" r="3" />
    <path d="M12 8v3M8 14l-2 2M12 14v2M16 14l2 2" />
  </svg>
);

export const VpnIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M12 2L3 7v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V7l-9-5z" />
    <path d="M9 12l2 2 4-4" />
  </svg>
);

export const DnsIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="12" r="10" />
    <path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
  </svg>
);

export const CdnIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="12" r="10" />
    <circle cx="12" cy="12" r="4" />
    <line x1="12" y1="2" x2="12" y2="8" />
    <line x1="12" y1="16" x2="12" y2="22" />
    <line x1="2" y1="12" x2="8" y2="12" />
    <line x1="16" y1="12" x2="22" y2="12" />
  </svg>
);

// ============================================
// SECURITY
// ============================================

export const SecurityIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M12 2l8 4v6c0 5.5-3.5 10-8 11-4.5-1-8-5.5-8-11V6l8-4z" />
    <path d="M9 12l2 2 4-4" />
  </svg>
);

export const LockIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="3" y="11" width="18" height="11" rx="2" />
    <path d="M7 11V7a5 5 0 0110 0v4" />
    <circle cx="12" cy="16" r="1" fill="currentColor" />
  </svg>
);

export const KeyIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
  </svg>
);

export const CertificateIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="3" y="3" width="18" height="14" rx="2" />
    <circle cx="12" cy="10" r="3" />
    <path d="M12 13v8M9 18l3 3 3-3" />
  </svg>
);

export const IdentityIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <circle cx="9" cy="10" r="2" />
    <path d="M15 8h3M15 12h3" />
    <path d="M6 16c0-1.5 1.5-3 3-3s3 1.5 3 3" />
  </svg>
);

// ============================================
// STORAGE
// ============================================

export const StorageIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M4 4h16v16H4z" />
    <path d="M4 9h16M4 14h16" />
    <circle cx="7" cy="6.5" r="0.5" fill="currentColor" />
    <circle cx="7" cy="11.5" r="0.5" fill="currentColor" />
    <circle cx="7" cy="16.5" r="0.5" fill="currentColor" />
  </svg>
);

export const S3Icon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12 2L4 6v12l8 4 8-4V6l-8-4zm0 2.18l5.78 2.89L12 9.96 6.22 7.07 12 4.18zM6 8.89l5 2.5v6.72l-5-2.5V8.89zm12 6.72l-5 2.5v-6.72l5-2.5v6.72z"/>
  </svg>
);

export const FileStorageIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
    <path d="M14 2v6h6M12 18v-6M9 15l3-3 3 3" />
  </svg>
);

// ============================================
// MONITORING & LOGGING
// ============================================

export const MonitoringIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="3" width="20" height="14" rx="2" />
    <path d="M8 21h8M12 17v4" />
    <path d="M6 10l3-3 3 3 6-6" />
  </svg>
);

export const AlertIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

export const LogsIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
    <path d="M14 2v6h6M8 13h8M8 17h8M8 9h2" />
  </svg>
);

export const MetricsIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M18 20V10M12 20V4M6 20v-6" />
  </svg>
);

// ============================================
// DEVOPS & CI/CD
// ============================================

export const GitIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M23.546 10.93L13.067.452a1.55 1.55 0 00-2.188 0L8.708 2.627l2.76 2.76a1.838 1.838 0 012.327 2.341l2.658 2.66a1.838 1.838 0 011.9 3.039 1.837 1.837 0 01-2.6 0 1.846 1.846 0 01-.404-2.019l-2.48-2.48v6.535a1.84 1.84 0 01.495.299 1.845 1.845 0 010 2.6 1.842 1.842 0 01-2.608 0 1.845 1.845 0 010-2.6c.179-.18.382-.318.602-.413v-6.598a1.834 1.834 0 01-.996-2.41l-2.72-2.72L.454 10.89a1.55 1.55 0 000 2.188l10.48 10.477a1.545 1.545 0 002.186 0l10.426-10.43a1.544 1.544 0 000-2.195"/>
  </svg>
);

export const JenkinsIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-1-13h2v6h-2zm0 8h2v2h-2z"/>
  </svg>
);

export const PipelineIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="4" cy="12" r="2" />
    <circle cx="12" cy="12" r="2" />
    <circle cx="20" cy="12" r="2" />
    <path d="M6 12h4M14 12h4" />
    <path d="M12 6v4M12 14v4" />
  </svg>
);

// ============================================
// MESSAGING & QUEUES
// ============================================

export const QueueIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="3" y="4" width="18" height="4" rx="1" />
    <rect x="3" y="10" width="18" height="4" rx="1" />
    <rect x="3" y="16" width="18" height="4" rx="1" />
  </svg>
);

export const KafkaIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
  </svg>
);

// ============================================
// USERS & ACCESS
// ============================================

export const UserIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="7" r="4" />
    <path d="M5.5 21v-2a4 4 0 014-4h5a4 4 0 014 4v2" />
  </svg>
);

export const UsersIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75" />
  </svg>
);

export const AdminIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="7" r="4" />
    <path d="M5.5 21v-2a4 4 0 014-4h5a4 4 0 014 4v2" />
    <path d="M22 2l-2 2m-7 7a5 5 0 11-7 7 5 5 0 017-7z" />
  </svg>
);

// ============================================
// API & INTEGRATIONS
// ============================================

export const ApiIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M4 6h16M4 12h16M4 18h10" />
    <circle cx="19" cy="18" r="2" />
  </svg>
);

export const WebhookIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M18 16.98h-5.99c-1.1 0-1.95.68-2.95 1.76C8.07 19.82 6.22 21 4 21c-1.38 0-2.5-.56-2.5-1.25 0-.69 1.12-1.25 2.5-1.25.34 0 .67.03.98.08" />
    <path d="M13 13.98c-.08.1-.15.19-.24.29-1.39 1.52-2.44 3.11-2.44 4.23 0 1.66 1.34 3 3 3s3-1.34 3-3c0-1.29-1.24-2.97-2.87-4.59" />
    <circle cx="13" cy="8" r="4" />
    <path d="M13 4v4" />
  </svg>
);

export const RestApiIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M17 3a2.828 2.828 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
    <path d="M15 5l4 4" />
  </svg>
);

export const GraphqlIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12.002 0a2.138 2.138 0 100 4.277 2.138 2.138 0 100-4.277zm8.54 4.931a2.138 2.138 0 100 4.277 2.138 2.138 0 100-4.277zm0 9.862a2.138 2.138 0 100 4.277 2.138 2.138 0 100-4.277zm-8.54 4.931a2.138 2.138 0 100 4.276 2.138 2.138 0 100-4.276zm-8.542-4.93a2.138 2.138 0 100 4.276 2.138 2.138 0 100-4.277zm0-9.863a2.138 2.138 0 100 4.277 2.138 2.138 0 100-4.277zm8.542-3.378L2.953 6.777v10.448l9.049 5.224 9.047-5.224V6.777zm0 1.601l7.66 13.27H4.34zm-1.387.371L3.97 15.037V7.363zm2.774 0l6.646 3.838v7.674zM5.355 17.44h13.293l-6.646 3.839z"/>
  </svg>
);

// ============================================
// GENERAL / MISC
// ============================================

export const GlobeIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="12" r="10" />
    <path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
  </svg>
);

export const CloudIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z" />
  </svg>
);

export const SettingsIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z" />
  </svg>
);

export const TerminalIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="4" width="20" height="16" rx="2" />
    <path d="M6 8l4 4-4 4M12 16h6" />
  </svg>
);

export const CodeIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <polyline points="16 18 22 12 16 6" />
    <polyline points="8 6 2 12 8 18" />
  </svg>
);

export const CheckIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

export const CloseIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);

export const PlusIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

export const MinusIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

export const ArrowRightIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
);

export const ArrowLeftIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="19" y1="12" x2="5" y2="12" />
    <polyline points="12 19 5 12 12 5" />
  </svg>
);

// ============================================
// FORTINET ICONS
// ============================================

export const FortinetIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12 2L2 7v10l10 5 10-5V7L12 2zm0 2.18l7.78 3.89L12 11.96 4.22 8.07 12 4.18zM4 9.89l7 3.5v6.72l-7-3.5V9.89zm16 6.72l-7 3.5v-6.72l7-3.5v6.72z"/>
  </svg>
);

export const FortigateIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="6" width="20" height="12" rx="2" />
    <path d="M6 10h12M6 14h8" />
    <circle cx="18" cy="14" r="1" fill="currentColor" />
  </svg>
);

// ============================================
// ARISTA ICONS
// ============================================

export const AristaIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
  </svg>
);

export const AristaSwitchIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="8" width="20" height="8" rx="1" />
    <circle cx="6" cy="12" r="1" fill="currentColor" />
    <circle cx="10" cy="12" r="1" fill="currentColor" />
    <circle cx="14" cy="12" r="1" fill="currentColor" />
    <circle cx="18" cy="12" r="1" fill="currentColor" />
    <path d="M4 8V6M8 8V4M12 8V6M16 8V4M20 8V6" />
  </svg>
);

// ============================================
// WINDOWS ICONS
// ============================================

export const WindowsIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M0 3.449L9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801"/>
  </svg>
);

export const WindowsServerIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="2" y="2" width="20" height="20" rx="2" />
    <path d="M2 11h9V2M13 2v9h9M2 13h9v9M13 13h9v9" />
  </svg>
);

// ============================================
// LINUX ICONS
// ============================================

export const LinuxIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12.504 0c-.155 0-.315.008-.48.021-4.226.333-3.105 4.807-3.17 6.298-.076 1.092-.3 1.953-1.05 3.02-.885 1.051-2.127 2.75-2.716 4.521-.278.832-.41 1.684-.287 2.489a.424.424 0 00-.11.135c-.26.268-.45.6-.663.839-.199.199-.485.267-.797.4-.313.136-.658.269-.864.68-.09.189-.136.394-.132.602 0 .199.027.4.055.536.058.399.116.728.04.97-.249.68-.28 1.145-.106 1.484.174.334.535.47.94.601.81.2 1.91.135 2.774.6.926.466 1.866.67 2.616.47.526-.116.97-.464 1.208-.946.587-.003 1.23-.269 2.26-.334.699-.058 1.574.267 2.577.2.025.134.063.198.114.333l.003.003c.391.778 1.113 1.132 1.884 1.071.771-.06 1.592-.536 2.257-1.306.631-.765 1.683-1.084 2.378-1.503.348-.199.629-.469.649-.853.023-.4-.2-.811-.714-1.376v-.097l-.003-.003c-.17-.2-.25-.535-.338-.926-.085-.401-.182-.786-.492-1.046h-.003c-.059-.054-.123-.067-.188-.135a.357.357 0 00-.19-.064c.431-1.278.264-2.55-.173-3.694-.533-1.41-1.465-2.638-2.175-3.483-.796-1.005-1.576-1.957-1.56-3.368.026-2.152.236-6.133-3.544-6.139zm.529 3.405h.013c.213 0 .396.062.584.198.19.135.33.332.438.533.105.259.158.459.166.724 0-.02.006-.04.006-.06v.105a.086.086 0 01-.004-.021l-.004-.024a1.807 1.807 0 01-.15.706.953.953 0 01-.213.335.71.71 0 00-.088-.042c-.104-.045-.198-.064-.284-.133a1.312 1.312 0 00-.22-.066c.05-.06.146-.133.183-.198.053-.128.082-.264.088-.402v-.02a1.21 1.21 0 00-.061-.4c-.045-.134-.101-.2-.183-.333-.084-.066-.167-.132-.267-.132h-.016c-.093 0-.176.03-.262.132a.8.8 0 00-.205.334 1.18 1.18 0 00-.09.468v.02c.002.134.017.267.051.399.009.054.027.105.05.154a.27.27 0 01-.09.042c-.052.021-.063.021-.138.063-.075-.043-.116-.129-.164-.206a1.544 1.544 0 01-.151-.705l-.004.024a.09.09 0 01-.004.021v-.105c0 .02.006.04.006.06.008-.264.061-.465.166-.724.108-.2.25-.398.438-.533.188-.136.371-.198.584-.198zm-1.603 5.027c.149.012.296.096.396.205.099.109.153.245.153.386v.001a.533.533 0 01-.146.38.534.534 0 01-.39.151h-.002a.536.536 0 01-.388-.152.533.533 0 01-.146-.38v-.001c0-.141.054-.278.152-.386a.533.533 0 01.383-.204h.001zm3.247 0a.534.534 0 01.383.204c.1.108.153.245.153.386v.001a.533.533 0 01-.146.38.534.534 0 01-.39.151h-.002a.535.535 0 01-.388-.152.533.533 0 01-.146-.38v-.001c0-.141.054-.278.152-.386a.534.534 0 01.384-.204zm-1.665 1.886a.742.742 0 01.274-.054.69.69 0 01.272.054.67.67 0 01.228.152.645.645 0 01.149.225.67.67 0 01.054.27.646.646 0 01-.054.27.645.645 0 01-.149.225.67.67 0 01-.228.152.69.69 0 01-.272.054.742.742 0 01-.274-.054.72.72 0 01-.231-.152.695.695 0 01-.152-.225.72.72 0 01-.055-.27.72.72 0 01.055-.27.695.695 0 01.152-.225.72.72 0 01.231-.152z"/>
  </svg>
);

// ============================================
// APACHE ICONS
// ============================================

export const ApacheIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 8.5a.75.75 0 01.75.75v4.5a.75.75 0 01-1.5 0v-4.5A.75.75 0 0112 8.5zm0 8a1 1 0 100-2 1 1 0 000 2z"/>
  </svg>
);

export const ApacheKafkaIcon: React.FC<IconProps> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z"/>
    <circle cx="12" cy="8" r="2"/>
    <circle cx="8" cy="14" r="2"/>
    <circle cx="16" cy="14" r="2"/>
    <path d="M12 10v2M10 13l-1 1M14 13l1 1"/>
  </svg>
);

// Export all icons as a map for easy access
export const iconMap: Record<string, React.FC<IconProps>> = {
  // Cloud
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

  // Security
  security: SecurityIcon,
  lock: LockIcon,
  key: KeyIcon,
  certificate: CertificateIcon,
  identity: IdentityIcon,

  // Storage
  storage: StorageIcon,
  s3: S3Icon,
  filestorage: FileStorageIcon,

  // Monitoring
  monitoring: MonitoringIcon,
  alert: AlertIcon,
  logs: LogsIcon,
  metrics: MetricsIcon,

  // DevOps
  git: GitIcon,
  jenkins: JenkinsIcon,
  pipeline: PipelineIcon,

  // Messaging
  queue: QueueIcon,
  kafka: KafkaIcon,

  // Users
  user: UserIcon,
  users: UsersIcon,
  admin: AdminIcon,

  // API
  api: ApiIcon,
  webhook: WebhookIcon,
  restapi: RestApiIcon,
  graphql: GraphqlIcon,

  // General
  globe: GlobeIcon,
  cloud: CloudIcon,
  settings: SettingsIcon,
  terminal: TerminalIcon,
  code: CodeIcon,
  check: CheckIcon,
  close: CloseIcon,
  plus: PlusIcon,
  minus: MinusIcon,
  arrowright: ArrowRightIcon,
  arrowleft: ArrowLeftIcon,

  // Vendor specific
  fortinet: FortinetIcon,
  fortigate: FortigateIcon,
  arista: AristaIcon,
  aristaswitch: AristaSwitchIcon,
  windows: WindowsIcon,
  windowsserver: WindowsServerIcon,
  linux: LinuxIcon,
  apache: ApacheIcon,
  apachekafka: ApacheKafkaIcon,
};

// Icon categories for the toolbar
export const iconCategories = {
  cloud: {
    label: 'Cloud Providers',
    icons: [
      { id: 'aws', label: 'AWS', color: '#FF9900' },
      { id: 'azure', label: 'Azure', color: '#0078D4' },
      { id: 'gcp', label: 'GCP', color: '#4285F4' },
      { id: 'digitalocean', label: 'DigitalOcean', color: '#0080FF' },
      { id: 'oracle', label: 'Oracle', color: '#F80000' },
    ],
  },
  containers: {
    label: 'Containers & Orchestration',
    icons: [
      { id: 'kubernetes', label: 'Kubernetes', color: '#326CE5' },
      { id: 'docker', label: 'Docker', color: '#2496ED' },
      { id: 'container', label: 'Container', color: '#6B7280' },
    ],
  },
  compute: {
    label: 'Compute',
    icons: [
      { id: 'server', label: 'Server', color: '#6B7280' },
      { id: 'vm', label: 'Virtual Machine', color: '#6B7280' },
      { id: 'lambda', label: 'Lambda/Function', color: '#FF9900' },
    ],
  },
  database: {
    label: 'Database',
    icons: [
      { id: 'database', label: 'Database', color: '#6B7280' },
      { id: 'postgres', label: 'PostgreSQL', color: '#336791' },
      { id: 'mysql', label: 'MySQL', color: '#4479A1' },
      { id: 'mongodb', label: 'MongoDB', color: '#47A248' },
      { id: 'redis', label: 'Redis', color: '#DC382D' },
    ],
  },
  networking: {
    label: 'Networking',
    icons: [
      { id: 'network', label: 'Network', color: '#6B7280' },
      { id: 'router', label: 'Router', color: '#6B7280' },
      { id: 'switch', label: 'Switch', color: '#6B7280' },
      { id: 'firewall', label: 'Firewall', color: '#EF4444' },
      { id: 'loadbalancer', label: 'Load Balancer', color: '#8B5CF6' },
      { id: 'vpn', label: 'VPN', color: '#10B981' },
      { id: 'dns', label: 'DNS', color: '#3B82F6' },
      { id: 'cdn', label: 'CDN', color: '#F59E0B' },
    ],
  },
  security: {
    label: 'Security',
    icons: [
      { id: 'security', label: 'Security', color: '#10B981' },
      { id: 'lock', label: 'Lock', color: '#6B7280' },
      { id: 'key', label: 'Key', color: '#F59E0B' },
      { id: 'certificate', label: 'Certificate', color: '#3B82F6' },
      { id: 'identity', label: 'Identity', color: '#8B5CF6' },
    ],
  },
  storage: {
    label: 'Storage',
    icons: [
      { id: 'storage', label: 'Storage', color: '#6B7280' },
      { id: 's3', label: 'S3/Object', color: '#569A31' },
      { id: 'filestorage', label: 'File Storage', color: '#6B7280' },
    ],
  },
  monitoring: {
    label: 'Monitoring',
    icons: [
      { id: 'monitoring', label: 'Monitoring', color: '#3B82F6' },
      { id: 'alert', label: 'Alert', color: '#EF4444' },
      { id: 'logs', label: 'Logs', color: '#6B7280' },
      { id: 'metrics', label: 'Metrics', color: '#10B981' },
    ],
  },
  devops: {
    label: 'DevOps',
    icons: [
      { id: 'git', label: 'Git', color: '#F05032' },
      { id: 'jenkins', label: 'Jenkins', color: '#D24939' },
      { id: 'pipeline', label: 'Pipeline', color: '#6B7280' },
    ],
  },
  messaging: {
    label: 'Messaging',
    icons: [
      { id: 'queue', label: 'Queue', color: '#6B7280' },
      { id: 'kafka', label: 'Kafka', color: '#231F20' },
    ],
  },
  users: {
    label: 'Users & Access',
    icons: [
      { id: 'user', label: 'User', color: '#6B7280' },
      { id: 'users', label: 'Users', color: '#6B7280' },
      { id: 'admin', label: 'Admin', color: '#EF4444' },
    ],
  },
  api: {
    label: 'APIs',
    icons: [
      { id: 'api', label: 'API', color: '#6B7280' },
      { id: 'restapi', label: 'REST API', color: '#10B981' },
      { id: 'graphql', label: 'GraphQL', color: '#E535AB' },
      { id: 'webhook', label: 'Webhook', color: '#F59E0B' },
    ],
  },
  vendors: {
    label: 'Vendors',
    icons: [
      { id: 'fortinet', label: 'Fortinet', color: '#EE3124' },
      { id: 'fortigate', label: 'FortiGate', color: '#EE3124' },
      { id: 'arista', label: 'Arista', color: '#0076CE' },
      { id: 'aristaswitch', label: 'Arista Switch', color: '#0076CE' },
    ],
  },
  os: {
    label: 'Operating Systems',
    icons: [
      { id: 'windows', label: 'Windows', color: '#0078D6' },
      { id: 'windowsserver', label: 'Windows Server', color: '#0078D6' },
      { id: 'linux', label: 'Linux', color: '#FCC624' },
    ],
  },
  general: {
    label: 'General',
    icons: [
      { id: 'globe', label: 'Globe/Internet', color: '#3B82F6' },
      { id: 'cloud', label: 'Cloud', color: '#6B7280' },
      { id: 'settings', label: 'Settings', color: '#6B7280' },
      { id: 'terminal', label: 'Terminal', color: '#1F2937' },
      { id: 'code', label: 'Code', color: '#6B7280' },
    ],
  },
};
