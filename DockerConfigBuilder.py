import os, chevron

class DockerConfigBuilder:
    def __init__(self):
        home_dir = self.env_var_exist('HOME') or '/home/feedbot'
        config_file_name = 'feed2discord.local.ini'
        self.conf_file_path = os.path.join(home_dir, config_file_name)

        self.set_config_from_env()
        self.generate_config_files()

    def set_config_from_env(self):
        self.debug = self.env_var_exist('DEBUG') or 2
        self.timezone = self.env_var_exist('TIMEZONE') or 'utc'
        self.login_token = self.env_var_exist('TOKEN') or ''
        self.db_path = self.env_var_exist('DB_PATH') or "feed2discord.db"
        self.publish = self.env_var_exist('PUBLISH') or 0
        self.start_skew_min = self.env_var_exist('SKEW_MIN') or 1
        self.rss_refresh_time = self.env_var_exist('REFRESH_TIME') or 900
        self.max_age = self.env_var_exist('MAX_AGE') or 86400
        self.one_send_typing = self.env_var_exist('ONE_SEND_TYPING') or 1 
        self.two_send_typing = self.env_var_exist('TWO_SEND_TYPING') or 0
        self.feeds = self.env_var_exist('FEEDS') or [
            {
                'name': 'my-super-feed',
                "channel": "FAKE_ID_CHANNEL",
                'url': 'https://www.cert.ssi.gouv.fr/alerte/feed/',
                'fields': 'guid,**title**,_published_,description'
            },
        ]
    def env_var_exist(self, env_name):
        if env_name in os.environ:
            return os.environ[env_name]

    def render_feeds(self):
        all_feeds = ''
        for feed in self.feeds:
            feeds_template = open('/opt/templates/feeds_template.ini', 'r')
            all_feeds += chevron.render(
                feeds_template, {
                    'feed_name': feed['name'],
                    'channel_id': feed['channel'],
                    'feed_url': feed['url'],
                    'fields': feed['fields'],
                })
        return all_feeds

    def render_all_conf(self):
        config_template = open('/opt/templates/config_template.ini', 'r')
        return chevron.render(
            config_template, {
                'debug': self.debug,
                'timezone': self.timezone,
                'login_token': self.login_token,
                'db_path': self.db_path,
                'publish': self.publish,
                'start_skew_min': self.start_skew_min,
                'rss_refresh_time': self.rss_refresh_time,
                'max_age': self.max_age,
                'one_send_typing': self.one_send_typing,
                'two_send_typing': self.two_send_typing,
                'feeds': self.render_feeds(),
            }
        )

    def generate_config_files(self):
        with open(self.conf_file_path, 'w') as config_file:
            config_file.write(self.render_all_conf())    

if __name__ == "__main__":
    DockerConfigBuilder()