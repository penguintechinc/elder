/**
 * Iconoir Icon Adapter
 * Imports icons from iconoir-react package and wraps them to conform to IconProps
 */

import { FC } from 'react';
import * as IconoirIcons from 'iconoir-react';
import type { IconProps, IconComponent, IconMap } from '../types';

/**
 * Wrapper function to create a normalized icon component from iconoir icon
 */
const createIconComponent = (IconComponent: any): IconComponent => {
  const WrappedIcon: FC<IconProps> = ({
    className,
    size = 24,
    color = '#8B5CF6',
  }) => {
    const sizeNum = typeof size === 'string' ? parseInt(size, 10) : size;
    return (
      <IconComponent
        width={sizeNum}
        height={sizeNum}
        color={color}
        strokeWidth={2}
        className={className}
      />
    );
  };
  WrappedIcon.displayName = `IconoirIcon`;
  return WrappedIcon;
};

/**
 * Iconoir Icon Map - 50+ useful icons categorized by type
 * Each icon is wrapped to accept standard IconProps (className, size, color)
 */
export const iconoirIconMap: IconMap = {
  // 3D & Design Icons
  'iconoir-3d-box': createIconComponent(IconoirIcons.Cube),
  'iconoir-design': createIconComponent(IconoirIcons.DesignNib),
  'iconoir-palette': createIconComponent(IconoirIcons.Palette),
  'iconoir-pen': createIconComponent(IconoirIcons.EditPencil),
  'iconoir-draw': createIconComponent(IconoirIcons.DesignPencil),

  // Navigation Icons
  'iconoir-navigation': createIconComponent(IconoirIcons.Navigator),
  'iconoir-arrow-up': createIconComponent(IconoirIcons.ArrowUp),
  'iconoir-arrow-down': createIconComponent(IconoirIcons.ArrowDown),
  'iconoir-arrow-left': createIconComponent(IconoirIcons.ArrowLeft),
  'iconoir-arrow-right': createIconComponent(IconoirIcons.ArrowRight),
  'iconoir-chevron-up': createIconComponent(IconoirIcons.NavArrowUp),
  'iconoir-chevron-down': createIconComponent(IconoirIcons.NavArrowDown),
  'iconoir-chevron-left': createIconComponent(IconoirIcons.NavArrowLeft),
  'iconoir-chevron-right': createIconComponent(IconoirIcons.NavArrowRight),
  'iconoir-expand': createIconComponent(IconoirIcons.Expand),
  'iconoir-collapse': createIconComponent(IconoirIcons.Collapse),
  'iconoir-zoom-in': createIconComponent(IconoirIcons.ZoomIn),
  'iconoir-zoom-out': createIconComponent(IconoirIcons.ZoomOut),

  // Actions Icons
  'iconoir-copy': createIconComponent(IconoirIcons.Copy),
  'iconoir-paste': createIconComponent(IconoirIcons.ClipboardCheck),
  'iconoir-trash': createIconComponent(IconoirIcons.Trash),
  'iconoir-download': createIconComponent(IconoirIcons.Download),
  'iconoir-upload': createIconComponent(IconoirIcons.Upload),
  'iconoir-save': createIconComponent(IconoirIcons.FloppyDisk),
  'iconoir-refresh': createIconComponent(IconoirIcons.Refresh),
  'iconoir-reload': createIconComponent(IconoirIcons.RefreshDouble),
  'iconoir-undo': createIconComponent(IconoirIcons.Undo),
  'iconoir-redo': createIconComponent(IconoirIcons.Redo),
  'iconoir-close': createIconComponent(IconoirIcons.Xmark),
  'iconoir-check': createIconComponent(IconoirIcons.Check),
  'iconoir-plus': createIconComponent(IconoirIcons.Plus),
  'iconoir-minus': createIconComponent(IconoirIcons.Minus),

  // Communication Icons
  'iconoir-mail': createIconComponent(IconoirIcons.Mail),
  'iconoir-message': createIconComponent(IconoirIcons.Message),
  'iconoir-chat': createIconComponent(IconoirIcons.ChatBubble),
  'iconoir-comment': createIconComponent(IconoirIcons.MessageText),
  'iconoir-phone': createIconComponent(IconoirIcons.Phone),
  'iconoir-bell': createIconComponent(IconoirIcons.Bell),
  'iconoir-notification': createIconComponent(IconoirIcons.BellNotification),
  'iconoir-share': createIconComponent(IconoirIcons.ShareAndroid),
  'iconoir-link': createIconComponent(IconoirIcons.Link),

  // Finance Icons
  'iconoir-money': createIconComponent(IconoirIcons.Coins),
  'iconoir-wallet': createIconComponent(IconoirIcons.Wallet),
  'iconoir-credit-card': createIconComponent(IconoirIcons.CreditCard),
  'iconoir-dollar': createIconComponent(IconoirIcons.Dollar),

  // System Icons
  'iconoir-server': createIconComponent(IconoirIcons.Server),
  'iconoir-database': createIconComponent(IconoirIcons.Database),
  'iconoir-cloud': createIconComponent(IconoirIcons.Cloud),
  'iconoir-settings': createIconComponent(IconoirIcons.Settings),
  'iconoir-tool': createIconComponent(IconoirIcons.Tools),
  'iconoir-wrench': createIconComponent(IconoirIcons.Wrench),
  'iconoir-power': createIconComponent(IconoirIcons.OnTag),
  'iconoir-battery': createIconComponent(IconoirIcons.BatteryFull),

  // Development Icons
  'iconoir-code': createIconComponent(IconoirIcons.Code),
  'iconoir-git': createIconComponent(IconoirIcons.Git),
  'iconoir-github': createIconComponent(IconoirIcons.Github),
  'iconoir-terminal': createIconComponent(IconoirIcons.Terminal),
  'iconoir-console': createIconComponent(IconoirIcons.Terminal),
  'iconoir-bug': createIconComponent(IconoirIcons.Bug),
  'iconoir-test-tube': createIconComponent(IconoirIcons.TestTube),

  // File & Document Icons
  'iconoir-file': createIconComponent(IconoirIcons.Page),
  'iconoir-folder': createIconComponent(IconoirIcons.Folder),
  'iconoir-document': createIconComponent(IconoirIcons.PageEdit),

  // User & Account Icons
  'iconoir-user': createIconComponent(IconoirIcons.User),
  'iconoir-profile': createIconComponent(IconoirIcons.ProfileCircle),
  'iconoir-people': createIconComponent(IconoirIcons.Group),
  'iconoir-team': createIconComponent(IconoirIcons.Group),
  'iconoir-lock': createIconComponent(IconoirIcons.Lock),
  'iconoir-unlock': createIconComponent(IconoirIcons.Lock),

  // Status & Feedback Icons
  'iconoir-success': createIconComponent(IconoirIcons.CheckCircle),
  'iconoir-error': createIconComponent(IconoirIcons.XmarkCircle),
  'iconoir-warning': createIconComponent(IconoirIcons.WarningTriangle),
  'iconoir-info': createIconComponent(IconoirIcons.InfoCircle),
  'iconoir-question': createIconComponent(IconoirIcons.HelpCircle),

  // Common UI Icons
  'iconoir-home': createIconComponent(IconoirIcons.Home),
  'iconoir-search': createIconComponent(IconoirIcons.Search),
  'iconoir-eye': createIconComponent(IconoirIcons.Eye),
  'iconoir-eye-off': createIconComponent(IconoirIcons.EyeClosed),
  'iconoir-star': createIconComponent(IconoirIcons.Star),
  'iconoir-heart': createIconComponent(IconoirIcons.Heart),
  'iconoir-bookmark': createIconComponent(IconoirIcons.Bookmark),
  'iconoir-flag': createIconComponent(IconoirIcons.Pin),
  'iconoir-filter': createIconComponent(IconoirIcons.Filter),
  'iconoir-list': createIconComponent(IconoirIcons.List),
  'iconoir-grid': createIconComponent(IconoirIcons.ViewGrid),
  'iconoir-table': createIconComponent(IconoirIcons.Table),
  'iconoir-menu': createIconComponent(IconoirIcons.Menu),
  'iconoir-more': createIconComponent(IconoirIcons.MoreVert),
  'iconoir-dots': createIconComponent(IconoirIcons.MoreVert),
};

/**
 * Get an icon component by ID
 */
export const getIconoirIcon = (id: string): IconComponent | undefined => {
  return iconoirIconMap[id];
};

/**
 * List all available iconoir icon IDs
 */
export const listIconoirIcons = (): string[] => {
  return Object.keys(iconoirIconMap);
};

export default iconoirIconMap;
