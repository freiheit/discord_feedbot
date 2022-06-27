import os, chevron, json

class DockerConfigBuilder:
    def __init__(self):
        home_dir = self.env_var_exist('HOME') or '/home/feedbot'
        config_file_name = 'feed2discord.local.ini'
        self.conf_file_path = os.path.join(home_dir, config_file_name)
        self.json_conf_path_dir = os.path.join('/config')
        self.set_config_from_env()
        self.generate_config_files()

    def set_config_from_env(self):
        self.debug = self.env_var_exist('DEBUG') or 2
        self.timezone = self.env_var_exist('TIMEZONE') or 'utc'
        self.db_path = self.env_var_exist('DB_PATH') or "feed2discord.db"
        self.publish = self.env_var_exist('PUBLISH') or 0
        self.start_skew_min = self.env_var_exist('SKEW_MIN') or 1
        self.rss_refresh_time = self.env_var_exist('REFRESH_TIME') or 900
        self.max_age = self.env_var_exist('MAX_AGE') or 86400
        self.one_send_typing = self.env_var_exist('ONE_SEND_TYPING') or 1 
        self.two_send_typing = self.env_var_exist('TWO_SEND_TYPING') or 0
        self.login_token, self.feeds  = self.parse_json_consig()

    def parse_json_consig(self):
        
        token = ''
        feeds = ''

        config_file_exist = os.path.exists(self.json_conf_path_dir)
        
        if config_file_exist:
            files_in_dir = os.listdir(self.json_conf_path_dir)
            config = ''
            
            for file in files_in_dir: 
                json_config_path = os.path.join(self.json_conf_path_dir, file)
                
                if os.path.isfile(json_config_path):
                    json_config_content = open(json_config_path,'r').read()
                    config = json.loads(json_config_content)
        
            token = config['token']
            feeds = config['feeds']

        elif self.env_var_exist('FEEDS') and self.env_var_exist('TOKEN'): 
            feeds = json.loads(self.env_var_exist('FEEDS'))
            token = self.env_var_exist('TOKEN')

        else: 
            print('\nYou must set a valid config file in /config dir')
            print('ex: --volume $(pwd)/test-config.json:/config/test-config.json\n')

            token = ''
            feeds = [
            {
                "name": "my-super-feed",
                "channel": "FAKE_ID_CHANNEL",
                "url": "https://www.cert.ssi.gouv.fr/alerte/feed/",
                "fields": "guid,**title**,_published_,description"
            },
        ]
        return token, feeds

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